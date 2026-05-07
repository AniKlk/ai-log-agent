"""
Logging Schema Knowledge for Azure App Insights and Infrastructure Logs

This module provides field definitions, table structures, and query patterns
for App Insights telemetry and Kubernetes infrastructure logs.
Non-technical users can intuitively describe their investigation intent,
and the agent maps those descriptions to correct tables, fields, and predicates.
"""


def get_app_insights_tables() -> dict:
    """Define App Insights tables, fields, and purposes."""
    return {
        "AppTraces": {
            "purpose": "Application logs and diagnostic traces from all services",
            "fields": {
                "TimeGenerated": "Timestamp of the log entry (UTC)",
                "Message": "Log message text (search for keywords here)",
                "SeverityLevel": "Log level: Verbose (0), Information (1), Warning (1), Error (2), Critical (3)",
                "_ResourceId": "Azure resource identifier (MOST RELIABLE service identifier)",
                "AppRoleName": "Service/app name (often empty, use _ResourceId instead)",
                "OperationName": "Operation or component name",
                "Properties": "Custom key-value pairs (dynamic type, use Properties.FieldName)",
            },
            "key_properties": [
                "Properties.ConfirmationCode",
                "Properties.ExamSessionId",
                "Properties.ClientCode",
                "Properties.ClientName",
            ],
            "example_queries": [
                "Search for errors: AppTraces | where SeverityLevel >= 2 | where TimeGenerated > ago(7d)",
                "Find by service: AppTraces | where _ResourceId contains 'candidate-app'",
                "Search text: AppTraces | where Message has 'error text'",
            ],
        },
        "AppExceptions": {
            "purpose": "Exception/error details from applications",
            "fields": {
                "TimeGenerated": "Timestamp of the exception (UTC)",
                "OuterMessage": "Exception message (top-level error)",
                "InnermostMessage": "Root cause exception message",
                "ProblemId": "Exception type grouping",
                "_ResourceId": "Azure resource identifier (service identifier)",
                "AppRoleName": "Service/app name",
                "Properties": "Custom properties (dynamic type)",
                "Stack": "Stack trace if available",
            },
            "key_properties": [
                "Properties.ConfirmationCode",
                "Properties.ExamSessionId",
            ],
            "example_queries": [
                "All exceptions in 7d: AppExceptions | where TimeGenerated > ago(7d) | summarize count() by ProblemId | order by count_ desc",
                "Candidate-app errors: AppExceptions | where _ResourceId contains 'candidate-app' | where TimeGenerated > ago(3d)",
                "Search exceptions: AppExceptions | where OuterMessage has 'search text' or InnermostMessage has 'search text'",
            ],
        },
        "AppRequests": {
            "purpose": "HTTP request tracking (response times, status codes, success/failure)",
            "fields": {
                "TimeGenerated": "Request timestamp",
                "Name": "Request/endpoint name",
                "Url": "Full URL",
                "Success": "true/false based on ResultCode",
                "ResultCode": "HTTP status code (200, 404, 500, etc.)",
                "DurationMs": "Response time in milliseconds",
                "_ResourceId": "Service identifier",
            },
            "example_queries": [
                "Failed requests: AppRequests | where TimeGenerated > ago(7d) | where Success == false | summarize count() by ResultCode",
                "Slow endpoints: AppRequests | where DurationMs > 5000 | where TimeGenerated > ago(1d)",
                "Request success rate: AppRequests | where TimeGenerated > ago(7d) | summarize SuccessPercent = (sum(iff(Success, 1, 0)) * 100 / count()) by Name",
            ],
        },
        "AppDependencies": {
            "purpose": "Dependency calls (database, external APIs, etc.) and their performance",
            "fields": {
                "TimeGenerated": "Call timestamp",
                "Name": "Dependency name",
                "Type": "Dependency type (Http, SQL, etc.)",
                "DurationMs": "Call duration in milliseconds",
                "Success": "true/false if dependency call succeeded",
                "_ResourceId": "Calling service identifier",
            },
            "example_queries": [
                "Slow Cosmos calls: AppDependencies | where Name contains 'Cosmos' | where DurationMs > 1000",
                "Failed dependencies: AppDependencies | where Success == false | summarize count() by Name, Type",
            ],
        },
        "AppEvents": {
            "purpose": "Custom business events (not exceptions, but domain-specific events)",
            "fields": {
                "TimeGenerated": "Event timestamp",
                "Name": "Event name (e.g., 'SessionStarted', 'ExamCompleted')",
                "Properties": "Custom event data (dynamic type)",
                "_ResourceId": "Source service identifier",
            },
            "example_queries": [
                "Custom events by type: AppEvents | where TimeGenerated > ago(7d) | summarize count() by Name | order by count_ desc",
            ],
        },
    }


def get_infrastructure_tables() -> dict:
    """Define Kubernetes and infrastructure log tables."""
    return {
        "KubeEvents": {
            "purpose": "Kubernetes cluster events (pod scheduling, failures, warnings)",
            "fields": {
                "TimeGenerated": "Event timestamp (UTC)",
                "Name": "Resource name (pod, node, etc.)",
                "Namespace": "Kubernetes namespace",
                "Reason": "Event reason code (FailedScheduling, CrashLoopBackOff, OOMKilling, Unhealthy, etc.)",
                "Message": "Human-readable event description",
                "Count": "How many times this event occurred",
                "Type": "Event type (Normal, Warning)",
            },
            "common_reasons": {
                "FailedScheduling": "Pod could not be scheduled on any node (insufficient resources)",
                "BackOff": "Pod backing off (repeated failures)",
                "CrashLoopBackOff": "Pod restarting repeatedly (crashes)",
                "ImagePullBackOff": "Failed to pull container image",
                "OOMKilling": "Pod killed due to out-of-memory",
                "Unhealthy": "Liveness or readiness probe failure",
                "FailedCreatePodSandbox": "Sandbox creation failure",
                "Evicted": "Pod evicted due to resource pressure",
            },
            "example_queries": [
                "All failures in 24h: KubeEvents | where TimeGenerated > ago(1d) | where Reason in ('Failed', 'BackOff', 'CrashLoopBackOff', 'OOMKilling')",
                "Pod crashes: KubeEvents | where Name contains 'app-proproctor' | where Reason == 'CrashLoopBackOff'",
                "Scheduling failures: KubeEvents | where Reason == 'FailedScheduling' | summarize count() by Message",
            ],
        },
        "ContainerLogV2": {
            "purpose": "Container stdout/stderr logs (application-level diagnostics)",
            "fields": {
                "TimeGenerated": "Log timestamp",
                "PodName": "Pod name",
                "ContainerName": "Container name within the pod",
                "LogLevel": "Log severity (Info, Warning, Error, Fatal, etc.)",
                "LogMessage": "Log message content",
                "ContainerStatus": "Container state (Running, Waiting, Terminated)",
            },
            "example_queries": [
                "Get all errors: ContainerLogV2 | where LogLevel in ('Error', 'ERROR', 'error', 'Fatal') | where TimeGenerated > ago(1d)",
                "Specific service: ContainerLogV2 | where PodName contains 'exam-sessions-api' | where TimeGenerated > ago(3d)",
                "Search message: ContainerLogV2 | where LogMessage has 'search text'",
            ],
        },
        "KubePodInventory": {
            "purpose": "Pod and container status snapshot (health, resources, conditions)",
            "fields": {
                "TimeGenerated": "Snapshot timestamp",
                "Name": "Pod name",
                "Namespace": "Kubernetes namespace",
                "PodStatus": "Pod phase (Running, Pending, Failed, Unknown, Succeeded)",
                "ContainerStatus": "Container state (Running, Waiting, Terminated)",
                "ContainerStatusReason": "Reason for container state (CrashLoopBackOff, ImagePullBackOff, etc.)",
                "ContainerRestartCount": "Number of times container has restarted",
                "ConditionReason": "Pod condition (MemoryPressure, CPUPressure, Ready, etc.)",
            },
            "pod_states": {
                "Running": "Pod is operating normally",
                "Pending": "Pod waiting to be scheduled or container starting",
                "Failed": "Pod exited with non-zero status",
                "Unknown": "Pod state could not be determined",
                "Succeeded": "Pod completed successfully (batch/job pods)",
            },
            "example_queries": [
                "Failed pods: KubePodInventory | where PodStatus in ('Failed', 'Unknown') | where TimeGenerated > ago(1d)",
                "Restart counts: KubePodInventory | where ContainerRestartCount > 2 | summarize by Name, ContainerRestartCount",
                "Resource pressure: KubePodInventory | where ConditionReason in ('MemoryPressure', 'CPUPressure', 'DiskPressure')",
            ],
        },
    }


def map_user_intent_to_table(intent: str) -> list[str]:
    """
    Map plain-language user intent to appropriate log tables.
    Returns list of recommended table names to query.
    """
    intent_lower = intent.lower()
    recommendations: list[str] = []

    # App Insights tables
    if any(word in intent_lower for word in ["error", "exception", "crash", "failed"]):
        recommendations.append("AppExceptions")
        recommendations.append("AppTraces")
    if any(word in intent_lower for word in ["warning", "warn"]):
        recommendations.append("AppTraces")
    if any(word in intent_lower for word in ["timeout", "slow", "latency", "performance"]):
        recommendations.append("AppRequests")
        recommendations.append("AppDependencies")
    if any(word in intent_lower for word in ["dependency", "external", "cosmos", "database"]):
        recommendations.append("AppDependencies")
    if any(word in intent_lower for word in ["gingerweb", "system check", "readiness", "face", "detect"]):
        recommendations.append("AppTraces")
    if any(word in intent_lower for word in ["lockdown", "process", "blocked app"]):
        recommendations.append("AppTraces")

    # Infrastructure tables
    if any(word in intent_lower for word in ["pod", "crash", "restart", "container"]):
        recommendations.append("KubeEvents")
        recommendations.append("KubePodInventory")
    if any(word in intent_lower for word in ["oom", "memory pressure", "cpu pressure"]):
        recommendations.append("KubeEvents")
        recommendations.append("KubePodInventory")
    if any(word in intent_lower for word in ["image pull", "pulling image"]):
        recommendations.append("KubeEvents")
    if any(word in intent_lower for word in ["logs", "log message"]):
        recommendations.append("ContainerLogV2")

    # Return deduplicated list
    return list(dict.fromkeys(recommendations))


def get_workspace_recommendation(intent: str) -> str:
    """
    Recommend which Log Analytics workspace to query.
    - proproctor: App Insights telemetry (backend + frontend services)
    - infrastructure: Kubernetes and cluster-level events
    """
    intent_lower = intent.lower()
    
    if any(word in intent_lower for word in ["pod", "kubernetes", "k8s", "infra", "node", "container crash", "restart", "oom", "memory pressure", "cpu pressure"]):
        return "infrastructure"
    
    # Default to proproctor for application-level queries
    return "proproctor"


def get_query_hint_for_intent(intent: str, workspace: str) -> str:
    """
    Provide a KQL query pattern hint based on user intent.
    """
    intent_lower = intent.lower()

    if workspace == "infrastructure":
        if "pod crash" in intent_lower or "crashloop" in intent_lower:
            return "KubeEvents | where Reason == 'CrashLoopBackOff' | where TimeGenerated > ago(7d)"
        if "pod restart" in intent_lower:
            return "KubePodInventory | where ContainerRestartCount > 0 | where TimeGenerated > ago(7d)"
        if "oom" in intent_lower or "out of memory" in intent_lower:
            return "KubeEvents | where Reason == 'OOMKilling' | where TimeGenerated > ago(7d)"
        if "image pull" in intent_lower:
            return "KubeEvents | where Reason == 'ImagePullBackOff' | where TimeGenerated > ago(7d)"
        if "failed scheduling" in intent_lower:
            return "KubeEvents | where Reason == 'FailedScheduling' | where TimeGenerated > ago(7d)"
    else:  # proproctor workspace
        if "error" in intent_lower or "exception" in intent_lower:
            return "union AppExceptions, AppTraces | where TimeGenerated > ago(7d) | where _ResourceId contains 'app-proproctor'"
        if "performance" in intent_lower or "slow" in intent_lower or "timeout" in intent_lower:
            return "AppRequests | where TimeGenerated > ago(7d) | where DurationMs > 1000 | summarize count() by Name | order by count_ desc"
        if "system check" in intent_lower or "gingerweb" in intent_lower:
            return "AppTraces | where _ResourceId contains 'candidate-app' | where Message has 'system check' or Message has 'GW' | where TimeGenerated > ago(7d)"
        if "lockdown" in intent_lower or "process block" in intent_lower:
            return "AppTraces | where _ResourceId contains 'candidate-app' | where Message has 'lockdown' or Message has 'blocked' | where TimeGenerated > ago(7d)"

    return "# Provide a KQL query based on your intent"
