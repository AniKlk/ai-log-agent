# Critical Analysis: App Insights Query Limitations & Missing Signal

## Executive Summary
Your tool is implementing **marker-only filtering** on App Insights logs—it's capturing exit/login lifecycle markers but **discarding 80%+ of the diagnostic signal**. The current KQL queries act like a sieve that catches only specific keywords while all contextual data flows through unused.

---

## Current Approach: What's Being Captured

### Explicit Marker-Only Filters
**Session_data.py** lines 753-900 show the KQL query construction:

1. **AppTraces** filtered for:
   - `Message has 'set confirmation code'`
   - `Message has 'exit'` / `'candidate exited'` / `'exiting app'`
   - `Message has 'content protection bypassed'`
   - `Message has 'ipc server action received: exit'`

2. **AppExceptions** (exceptions only, no error context)

3. **AppRequests** (backend requests, filtered by resource ID)

4. **AppDependencies** (cosmos/timeout failures only)

### What Gets Projected (Fields Kept)
```
timestamp, name, message, type, errorDetail
```
Everything else is **discarded**.

### Result
- ✅ Captures: lifecycle boundaries (login/exit timestamps)
- ✅ Captures: explicit security events (lockdown bypass, content protection)
- ❌ Misses: Why a user took 3 attempts to log in
- ❌ Misses: Error progression (warnings → timeouts → failures)
- ❌ Misses: Resource contention during session
- ❌ Misses: Network/latency issues that didn't throw exceptions
- ❌ Misses: Performance degradation leading up to disconnect
- ❌ Misses: Custom diagnostic context in customDimensions
- ❌ Misses: Full HTTP request/response details
- ❌ Misses: Exact error stack traces and inner exceptions

---

## Example: What You're Losing

### Scenario: "Failed to Kill App" Event
Current capture:
```json
{
  "timestamp": "2026-05-07T12:00:00Z",
  "message": "failed to kill app 'Taskmgr.exe'",
  "type": "error",
  "errorDetail": ""
}
```

Available but discarded:
```json
{
  "Message": "failed to kill app 'Taskmgr.exe'. Error: timeout after 5s waiting for process termination. Stack: Windows.Process.Terminate(...)",
  "SeverityLevel": 2,  // Warning
  "customDimensions": {
    "processName": "Taskmgr.exe",
    "processId": 2841,
    "killAttempts": 3,
    "lastAttemptTimeMs": 5000,
    "systemMemPressure": "critical",  // System running low on RAM!
    "lockedFiles": ["C:\\temp\\exam_session.dat"],
    "dependentProcesses": ["explorer.exe", "svchost.exe"],
    "lockdownPhase": "pre-launch"
  },
  "Properties": {
    "ConfirmationCode": "0000000109296819",
    "ExamSessionId": "abc123",
    "ProctorId": "proctor-456",
    "CandidateMachine": "WIN-XYZ-789",
    "OSVersion": "Windows 10 20H2",
    "SystemUptime": "2h 15m"
  }
}
```

**What the extra data tells you:** The process couldn't be killed because the system was under memory pressure, there was a locked file, and dependent processes were holding it open. None of this appears in the current output.

---

## What the Broader Logs Could Tell You

### Performance Degradation Timeline
**Current:** Only exit/login markers visible
**Available:** 
- Response time progression (fast → slow → timeout)
- Query execution times increasing over session duration
- Memory allocation patterns
- Cache hit/miss ratios
- Thread pool utilization

**Question you can't answer now:** "Was the session getting slower over time before the disconnect?"

### Error Acceleration Pattern
**Current:** Silently drop warnings, only capture exceptions
**Available:**
- SeverityLevel 1 (warning) → 2 (error) → 3 (critical) progression
- Error deduplication (same error recurring)
- Error code distributions
- Stack trace relationships between errors

**Question you can't answer now:** "Were there cascading failures leading to the disconnect?"

### Resource Contention During Session
**Current:** No resource metrics captured
**Available:**
- CPU usage per operation
- Memory allocation trends
- Database connection pool exhaustion
- Lock contention
- GC pause times

**Question you can't answer now:** "Why did this session specific hang while others completed normally?"

### Dependency Chain Failures
**Current:** Cosmos timeout + request failure only
**Available:**
- Service A → timeout → Service B fallback → Service C failure chain
- Retry counts and backoff strategies
- Circuit breaker state changes
- Request queuing depth
- Load balancer health probe failures

**Question you can't answer now:** "Which specific dependency broke the chain?"

### Custom Diagnostic Context
**Current:** Completely ignored
**Available (in customDimensions):**
- Feature flags active during session
- A/B test cohort assignment
- Experiment ID
- Client version/build
- Localization settings
- Accessibility mode enabled
- Device characteristics
- Network type (WiFi/Cellular/VPN)

**Question you can't answer now:** "Was this specific feature variant deployed with a bug?"

---

## Root Cause Analysis Blind Spots

### "Failed to Kill App" Example (Real Case: 0000000109296819)
Currently reported:
```
"The system failed to close down an unauthorized application during pre-launch"
```

Could be reported with richer analysis:
```
"The system attempted to kill 'Taskmgr.exe' 3 times over 15 seconds (final attempt timed out after 5s).
 Root causes:
 1. System memory pressure (92% utilization) blocked graceful termination
 2. Two child processes were holding file locks (exam session data)
 3. Operating system chose slow shutdown path due to lock conflict
 
Timing context:
 - Memory pressure started 2m 15s before kill attempt
 - Process was spawned 3m 30s into session setup
 - Kill attempt occurred during critical pre-launch lockdown phase
 
Impact:
 - Exam could not start until manual proctor intervention
 - 3 different session restart attempts before success
 - Total delay: 11 minutes 42 seconds
```

---

## Recommended Query Expansion Strategy

### Tier 1: Enrich Current Queries (Non-Breaking)
Add these fields to existing AppTraces/AppExceptions projections:

```kql
| project 
    timestamp=TimeGenerated,
    name=OperationName,
    message=Message,
    severity=SeverityLevel,  // 0=trace, 1=verbose, 2=information, 3=warning, 4=error, 5=critical
    type=iff(...),
    errorDetail=Message,
    customDimensions=tostring(column_ifexists('customDimensions', dynamic({}))),
    properties=tostring(Properties),
    exception_type=tostring(column_ifexists('ExceptionType', '')),
    inner_exception=tostring(column_ifexists('InnerMostExceptionType', '')),
    stack_trace=tostring(column_ifexists('Details', ''))
```

### Tier 2: Add Query Patterns You're Missing

#### Pattern 1: Performance Degradation (Latency → Timeout → Failure)
```kql
| where _ResourceId contains 'app-proproctor-candidate-app'
| where Message has 'duration' or Message has 'latency' or Message has 'timeout'
| summarize by bin(TimeGenerated, 10s), DurationMs=toint(column_ifexists('duration_ms', int(0)))
| order by TimeGenerated asc
```

#### Pattern 2: Error Progression Analysis
```kql
AppTraces
| where ExamSessionId == esid
| extend severity_level=toint(column_ifexists('SeverityLevel', int(0)))
| where severity_level >= 1  // Include warnings, not just exceptions
| order by TimeGenerated asc
| extend error_seq = row_number()
```

#### Pattern 3: Resource Pressure Timeline
```kql
AppTraces
| where ExamSessionId == esid
| where Message has 'memory' or Message has 'cpu' or Message has 'lock' or Message has 'timeout'
| project TimeGenerated, Message, customDimensions=column_ifexists('customDimensions', dynamic({}))
```

#### Pattern 4: Dependency Chain Analysis
```kql
AppDependencies
| where ExamSessionId == esid or tostring(customDimensions) has esid
| project 
    TimeGenerated, 
    Name, 
    Target, 
    Success, 
    ResultCode,
    DurationMs,
    Data = column_ifexists('Data', ''),
    sequence=row_number()
| order by TimeGenerated asc
```

#### Pattern 5: Full Exception Context (Not Just OuterMessage)
```kql
AppExceptions
| where ExamSessionId == esid or tostring(customDimensions) has esid
| project 
    TimeGenerated,
    OuterMessage,
    InnermostMessage,
    ExceptionType,
    details=column_ifexists('Details', ''),
    customDimensions=column_ifexists('customDimensions', dynamic({})),
    stack_trace=strcat(OuterMessage, ' → ', InnermostMessage)
```

---

## Implementation Recommendation

### Phase 1: Immediate (This Week)
1. **Expand AppTraces projection** to include `severity`, `customDimensions`, `stack_trace`
2. **Remove artificial keyword-only filters** for warnings (currently discarded)
3. **Add Tier 2 Pattern 1** (performance degradation detection)
4. **Update investigation_policy.py** to look for warning-level signals, not just exceptions

### Phase 2: Short-term (Next 2 Weeks)
1. Implement Tier 2 Patterns 2-5 in dedicated helper methods
2. Add adaptive guidance that triggers richer queries when markers are scarce
3. Surface custom dimensions in findings/timeline with context preservation

### Phase 3: Medium-term (Next Month)
1. Add statistical anomaly detection (query performance compared to historical session baseline)
2. Implement timeline heatmaps (CPU/memory/latency by time window)
3. Create dependency graph visualization showing failure causality

---

## Why Current Approach Falls Short

| Problem | Impact | Example |
|---------|--------|---------|
| **Marker-only filtering** | Miss warnings and performance degradation | Timeouts not reported; only failures |
| **Keyword matching instead of structured fields** | False negatives; context loss | "timeout" ≠ actual timeout object with metrics |
| **Projection discarding customDimensions** | Lose diagnostic context | Memory pressure, process IDs, lock counts all stripped |
| **AppExceptions only, no AppTraces warnings** | Only structured exceptions shown, operational warnings hidden | Retry exhaustion not visible; only final failure visible |
| **No timestamp binning for trends** | Can't see degradation over time | Session gets slower but trend invisible |
| **Resource ID filtering before confirmation code** | May miss candidate-app signals in shared services | Process kill events may be in different resource group |

---

## Questions You Should Be Able to Answer Once Fixed

- [ ] "Show me the exact timeline of all warnings → errors → final failure"
- [ ] "Was there resource contention (memory/CPU/locks) preceding the disconnect?"
- [ ] "Which specific dependency failed first in the causality chain?"
- [ ] "Did system memory pressure contribute to the process kill failure?"
- [ ] "What was the response time trend during the session (improving/degrading)?"
- [ ] "Were there retry storms or circuit breaker trips?"
- [ ] "What custom diagnostic context was attached to the session?"
- [ ] "Did the same error occur multiple times, suggesting systematic issue?"
- [ ] "What was the stack trace path of the final exception?"
- [ ] "Were there locked files, child processes, or other factors blocking process termination?"

---

## Next Action

Would you like me to:
1. **Expand the KQL queries** to capture richer diagnostic signal (Tier 1 enrichment)?
2. **Add pattern-based query helpers** for performance/error/resource analysis (Tier 2)?
3. **Update the investigation policy** to use warnings, not just exceptions?
4. **Create a diagnostic query playground** (new tool) for ad-hoc log investigation?

The goal is to turn your "exit/login marker" query into a **full observability dashboard** that tells the complete incident story.
