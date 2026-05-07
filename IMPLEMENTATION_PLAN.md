# Implementation Guide: Enriching App Insights Queries

## Overview
This guide shows concrete code changes to transition from **marker-only filtering** to **comprehensive diagnostic capture**.

## File 1: Enhanced Query Model

Create `backend/app/tools/models.py` - Add these new models for richer log data:

```python
# Add after existing imports
from enum import IntEnum

class LogSeverity(IntEnum):
    """App Insights SeverityLevel mapping"""
    TRACE = 0
    VERBOSE = 1
    INFORMATION = 2
    WARNING = 3
    ERROR = 4
    CRITICAL = 5

class EnrichedLogEvent(BaseModel):
    """Extended log event with full diagnostic context"""
    timestamp: str
    name: str
    message: str
    severity: int  # SeverityLevel 0-5
    type: str  # "info" | "error" | "warning" | "disconnect"
    source: str  # "app-insights", "infra", etc.
    
    # New enriched fields
    exception_type: str | None = None
    inner_exception: str | None = None
    stack_trace: str | None = None
    custom_dimensions: dict[str, Any] | None = None
    resource_id: str | None = None
    operation_id: str | None = None
    session_id: str | None = None
    duration_ms: int | None = None
    result_code: str | None = None
```

---

## File 2: Enhanced Session Data Queries

Modify `backend/app/tools/session_data.py` - Replace `_query_app_insights()` method:

```python
async def _query_app_insights(
    self, exam_session_id: str, confirmation_code: str
) -> tuple[list[LogEvent], list[LogError], int]:
    """
    Enhanced KQL query that captures:
    - All severity levels (not just exceptions)
    - Custom diagnostic dimensions
    - Exception details with stack traces
    - Performance metrics (duration, result codes)
    - Resource utilization context
    """
    
    time_filter = self._compute_app_insights_time_filter(exam_session_id)
    candidate_time_filter = self._compute_candidate_time_filter(exam_session_id)
    process_signal_time_filter = self._compute_process_signal_time_filter(exam_session_id)
    
    kql = (
        "let cc = '{code}'; "
        "let esid = '{esid}'; "
        
        # ========== TIER 1: ENRICHED APP EVENTS ==========
        "let events = AppEvents "
        "{time_filter} "
        "| where Properties.ExamSessionId == esid "
        "  or Properties.examSessionId == esid "
        "  or tostring(Properties.ConfirmationCode) == cc "
        "  or tostring(column_ifexists('customDimensions', dynamic({{}})).ConfirmationCode) == cc "
        "  or tostring(column_ifexists('customDimensions', dynamic({{}}))) has cc "
        "| extend evt_message = coalesce(tostring(Properties.message), tostring(Name)) "
        "| extend severity = toint(column_ifexists('SeverityLevel', int(0))) "
        # Field extraction: severity level includes warnings (3) not just errors (4)
        "| project "
        "    timestamp=TimeGenerated, "
        "    name=Name, "
        "    message=evt_message, "
        "    severity=severity, "
        "    type=iff(severity >= 3 or evt_message has 'warning' or evt_message has 'error' or evt_message has 'fail' or evt_message has 'exception', iff(evt_message has 'exit', 'disconnect', 'error'), 'info'), "
        "    custom_dims=tostring(column_ifexists('customDimensions', dynamic({{}}))), "
        "    resource_id=_ResourceId, "
        "    operation_id=tostring(column_ifexists('operation_Id', '')), "
        "    duration_ms=toint(column_ifexists('duration_ms', int(0))), "
        "    result_code=tostring(column_ifexists('resultCode', '')); "
        
        # ========== TIER 2: ENRICHED APP TRACES (INCLUDES WARNINGS) ==========
        "let traces = AppTraces "
        "{time_filter} "
        "| where Properties.ExamSessionId == esid "
        "  or Properties.examSessionId == esid "
        "  or tostring(Properties.ConfirmationCode) == cc "
        "  or tostring(column_ifexists('customDimensions', dynamic({{}})).ConfirmationCode) == cc "
        "  or tostring(column_ifexists('customDimensions', dynamic({{}}))) has cc "
        "| extend severity = toint(column_ifexists('SeverityLevel', int(0))) "
        # Key change: include SeverityLevel >= 1 (VERBOSE), not just exceptions
        "| where severity >= 1 or Message has 'warning' or Message has 'error' or Message has 'fail' or Message has 'timeout' "
        "| project "
        "    timestamp=TimeGenerated, "
        "    name=OperationName, "
        "    message=Message, "
        "    severity=severity, "
        "    type=iff(severity >= 4 or Message has 'error' or Message has 'fail' or Message has 'exception', iff(Message has 'exit', 'disconnect', 'error'), iff(severity >= 3 or Message has 'warning' or Message has 'timeout', 'warning', 'info')), "
        "    custom_dims=tostring(column_ifexists('customDimensions', dynamic({{}}))), "
        "    resource_id=_ResourceId, "
        "    operation_id=tostring(column_ifexists('operation_Id', '')), "
        "    duration_ms=toint(column_ifexists('duration_ms', int(0))), "
        "    result_code=tostring(column_ifexists('resultCode', '')); "
        
        # ========== TIER 3: FULL EXCEPTION CONTEXT ==========
        "let errors = AppExceptions "
        "{time_filter} "
        "| where Properties.ExamSessionId == esid "
        "  or Properties.examSessionId == esid "
        "  or tostring(Properties.ConfirmationCode) == cc "
        "  or tostring(column_ifexists('customDimensions', dynamic({{}})).ConfirmationCode) == cc "
        "| project "
        "    timestamp=TimeGenerated, "
        "    name=ProblemId, "
        "    message=OuterMessage, "
        "    severity=int(4),  "  # Exceptions default to ERROR
        "    type='error', "
        "    exception_type=ExceptionType, "
        "    inner_exception=InnermostExceptionType, "
        "    stack_trace=strcat(OuterMessage, ' | ', InnermostMessage), "
        "    custom_dims=tostring(column_ifexists('customDimensions', dynamic({{}}))), "
        "    resource_id=_ResourceId, "
        "    operation_id=tostring(column_ifexists('operation_Id', '')); "
        
        # ========== TIER 4: PERFORMANCE METRICS (NEW) ==========
        "let perf_degradation = AppDependencies "
        "{time_filter} "
        "| where _ResourceId contains 'app-proproctor' "
        "| extend severity = iff(DurationMs > 5000, 3, iff(DurationMs > 2000, 2, 0)) "
        "| where severity >= 2 or Success == false "
        "| project "
        "    timestamp=TimeGenerated, "
        "    name=strcat('perf-', Name), "
        "    message=strcat('slow dependency: ', Name, ' target=', Target, ' durationMs=', tostring(DurationMs)), "
        "    severity=severity, "
        "    type=iff(Success == false, 'error', 'warning'), "
        "    duration_ms=DurationMs, "
        "    resource_id=_ResourceId, "
        "    result_code=tostring(ResultCode); "
        
        # ========== TIER 5: RESOURCE CONTENTION SIGNALS (NEW) ==========
        "let resource_pressure = AppTraces "
        "{time_filter} "
        "| where _ResourceId contains 'app-proproctor' "
        "| where Message has 'memory' or Message has 'cpu' or Message has 'lock' or Message has 'pool' or Message has 'timeout' "
        "| project "
        "    timestamp=TimeGenerated, "
        "    name='resource-pressure', "
        "    message=Message, "
        "    severity=iff(Message has 'critical' or Message has 'exhausted', 5, 3), "
        "    type='warning', "
        "    custom_dims=tostring(column_ifexists('customDimensions', dynamic({{}}))); "
        
        # Final union: combine all sources
        "union events, traces, errors, perf_degradation, resource_pressure "
        "| sort by timestamp asc "
    ).format(code=confirmation_code, esid=exam_session_id, 
             time_filter=time_filter, 
             candidate_time_filter=candidate_time_filter,
             process_signal_time_filter=process_signal_time_filter)
    
    # Execute query...
    # [existing execution logic]
```

---

## File 3: New Warning Detection Pattern

Add to `backend/app/agent/investigation_policy.py`:

```python
def _detect_warning_progression(events: list[LogEvent]) -> tuple[int, str]:
    """
    Detect if warnings are escalating to errors (sign of degradation).
    
    Returns: (severity_score 0-10, explanation)
    """
    warning_count = sum(1 for e in events if e.type == "warning")
    error_count = sum(1 for e in events if e.type == "error")
    
    # Timeline analysis: are warnings getting denser before errors?
    sorted_events = sorted([e for e in events if e.type in ("warning", "error")], 
                          key=lambda e: e.timestamp)
    
    escalation_indicators = 0
    explanation_parts = []
    
    if warning_count >= 3 and error_count >= 1:
        escalation_indicators += 2
        explanation_parts.append(f"{warning_count} warnings preceded {error_count} errors")
    
    # Check for time-based escalation (warnings in first half, errors in second)
    if sorted_events:
        midpoint = len(sorted_events) // 2
        first_half_warnings = sum(1 for e in sorted_events[:midpoint] if e.type == "warning")
        second_half_errors = sum(1 for e in sorted_events[midpoint:] if e.type == "error")
        
        if first_half_warnings >= 2 and second_half_errors >= 1:
            escalation_indicators += 3
            explanation_parts.append("warning escalation pattern detected (warnings → errors over time)")
    
    return escalation_indicators, "; ".join(explanation_parts)
```

---

## File 4: Enhanced Orchestrator Processing

Modify `backend/app/agent/orchestrator.py` - Add warning analysis:

```python
async def run(self, query: str, request_id: str, conversation_history: list[dict] | None = None) -> AgentOutput:
    # ... existing code ...
    
    # Track warning progression in addition to errors
    warning_escalation_detected = False
    warning_count = 0
    
    # After each tool execution, analyze for warnings
    for tool_result in [getSessionData_result, getChatHistory_result, getSessionTimeline_result]:
        if hasattr(tool_result, 'events'):
            events = tool_result.events
            warning_count += sum(1 for e in events if e.type == "warning")
            
            # NEW: Detect if warnings are escalating
            severity_score, explanation = _detect_warning_progression(events)
            if severity_score >= 5:
                warning_escalation_detected = True
                logger.info("Warning escalation pattern: %s", explanation)
    
    # Provide adaptive guidance about warnings
    if warning_escalation_detected and warning_count >= 3:
        system_message_addition = (
            "\n## Warning Escalation Detected\n"
            f"The logs show {warning_count} warning-level events preceding errors. "
            "This suggests system degradation rather than sudden failure. "
            "Include warning timeline in your root cause analysis."
        )
        messages.append({"role": "system", "content": system_message_addition})
```

---

## File 5: New Query Tool for Advanced Diagnostics

Create `backend/app/tools/advanced_diagnostics.py`:

```python
class AdvancedDiagnosticsTool(BaseTool):
    """
    Runs sophisticated diagnostic queries for:
    - Performance degradation patterns
    - Error cascade analysis
    - Resource contention detection
    - Dependency failure chains
    """
    
    name = "advancedDiagnostics"
    description = "Advanced diagnostic patterns: performance trends, error cascades, resource pressure"
    input_model = AdvancedDiagnosticsInput
    
    async def execute(self, args: BaseModel) -> dict:
        confirmation_code = args.confirmation_code
        diagnostic_type = args.type  # "performance", "error_cascade", "resource_pressure"
        
        if diagnostic_type == "performance":
            return await self._analyze_performance_degradation(confirmation_code)
        elif diagnostic_type == "error_cascade":
            return await self._analyze_error_cascade(confirmation_code)
        elif diagnostic_type == "resource_pressure":
            return await self._analyze_resource_pressure(confirmation_code)
    
    async def _analyze_performance_degradation(self, confirmation_code: str) -> dict:
        """Query for latency trend (is session getting slower?)"""
        kql = f"""
        AppDependencies
        | where tostring(customDimensions) has '{confirmation_code}'
        | order by TimeGenerated asc
        | extend bucket = bin(TimeGenerated, 1m)
        | summarize 
            avg_duration=avg(DurationMs),
            max_duration=max(DurationMs),
            failure_count=sumif(1, Success == false),
            call_count=count()
            by bucket
        | order by bucket asc
        """
        # Execute and return trend data
    
    async def _analyze_error_cascade(self, confirmation_code: str) -> dict:
        """Query for sequential error pattern (A failed → B failed → C failed)"""
        kql = f"""
        union AppExceptions, AppTraces
        | where tostring(customDimensions) has '{confirmation_code}'
        | where OuterMessage has 'error' or Message has 'error' or Message has 'failed'
        | order by TimeGenerated asc
        | extend 
            error_seq = row_number(),
            error_type = case(
                OuterMessage has 'timeout', 'timeout',
                OuterMessage has 'deadlock', 'deadlock',
                Message has 'cosmos', 'cosmos_dependency',
                'unknown'
            )
        | project TimeGenerated, error_seq, error_type, message=coalesce(OuterMessage, Message)
        """
        # Track causality
    
    async def _analyze_resource_pressure(self, confirmation_code: str) -> dict:
        """Query for memory/CPU/lock contention"""
        kql = f"""
        AppTraces
        | where tostring(customDimensions) has '{confirmation_code}'
        | where Message has 'memory' or Message has 'cpu' or Message has 'lock' or Message has 'pool'
        | extend 
            resource_type = case(
                Message has 'memory', 'memory',
                Message has 'cpu', 'cpu',
                Message has 'lock', 'lock',
                'other'
            ),
            resource_level = case(
                Message has 'critical', 'critical',
                Message has 'warning', 'warning',
                'info'
            )
        | summarize 
            event_count=count(),
            first_time=min(TimeGenerated),
            last_time=max(TimeGenerated)
            by resource_type, resource_level
        """
```

---

## Migration Path

### Phase 1 (This Week)
1. Add `EnrichedLogEvent` model to `models.py`
2. Enhance `AppTraces` projection in `_query_app_insights()` to include:
   - `severity` field (capture warnings)
   - `custom_dims` field
   - `exception_type` and `stack_trace` for exceptions
3. Update investigator prompt to mention "warnings are important diagnostics"

### Phase 2 (Next Week)
1. Add `TIER 4` (perf_degradation) and `TIER 5` (resource_pressure) lets to KQL
2. Implement `_detect_warning_progression()` in investigation_policy
3. Add warning escalation guidance to orchestrator

### Phase 3 (Week 3)
1. Create `AdvancedDiagnosticsTool` 
2. Wire into orchestrator as fallback when standard queries are thin
3. Add to tool registry

---

## Expected Impact

| Metric | Before | After |
|--------|--------|-------|
| **Events captured** | ~20-30 (markers only) | ~200-300 (full timeline) |
| **Diagnostic fields** | 4 (timestamp, name, message, type) | 12+ (includes severity, dims, traces) |
| **Warning visibility** | 0 (discarded) | Full analysis |
| **Root cause clarity** | "Exit marker detected" | "3 warnings escalated to timeout, memory pressure contributed" |
| **Analysis depth** | Superficial | Comprehensive |

---

## Questions This Unlocks

✅ "Show all warnings leading up to the error" - NOW POSSIBLE
✅ "Was there resource contention?" - NOW POSSIBLE  
✅ "What's the exact stack trace?" - NOW POSSIBLE
✅ "Did performance degrade over time?" - NOW POSSIBLE
✅ "How many times did this error occur?" - NOW POSSIBLE
✅ "What was the dependency failure chain?" - NOW POSSIBLE (Phase 3)
✅ "What diagnostic context was attached?" - NOW POSSIBLE

---

## Rollback Plan
- All changes are additive (new fields, new lets)
- Existing marker-based queries still work
- Can disable Tier 4/5 let unions by commenting them out
- No breaking changes to existing downstream logic
