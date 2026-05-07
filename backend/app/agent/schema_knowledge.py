"""
Schema knowledge for ProProctor databases, containers, and field mappings.

This module encodes the database structure, field definitions, query patterns,
and data mapping information so the agent can intelligently construct queries
and understand schema constraints without manual explanation.
"""

from dataclasses import dataclass
from typing import Dict, List, Set


@dataclass
class CosmosField:
    """Describes a single field in a Cosmos container."""

    name: str
    type_hint: str  # e.g., "string", "integer", "ISO 8601", "array"
    description: str
    is_indexed: bool = True
    examples: List[str] | None = None


@dataclass
class CosmosContainer:
    """Describes a Cosmos DB container with its schema."""

    database: str
    container: str
    purpose: str
    key_fields: List[str]  # Primary identifiers
    fields: Dict[str, CosmosField]
    query_patterns: List[str]  # Common query patterns for this container
    common_filters: Dict[str, str]  # Common field:example_value pairs


# =============================================================================
# COSMOS DB SCHEMA DEFINITIONS
# =============================================================================

EXAM_SESSION_CONTAINER = CosmosContainer(
    database="ExamSession",
    container="exam-session",
    purpose="Exam session root records with status, metadata, and configuration",
    key_fields=["id", "Id", "ConfirmationCode", "ExamSessionId"],
    fields={
        "Id": CosmosField(
            "Id",
            "GUID string",
            "Unique exam session identifier (same as ExamSessionId)",
        ),
        "ConfirmationCode": CosmosField(
            "ConfirmationCode",
            "16-digit numeric string",
            "Human-readable session identifier (e.g., 1234567890123456)",
        ),
        "Status": CosmosField(
            "Status",
            "string enum",
            "Session status (Created, Started, InProgress, Completed, Disconnected, etc.)",
            examples=["Created", "Started", "InProgress", "Completed", "Disconnected"],
        ),
        "TestStatus": CosmosField(
            "TestStatus",
            "string enum",
            "Exam completion status (Completed, Failed, InProgress, etc.)",
            examples=["Completed", "InProgress", "Failed"],
        ),
        "Candidate.Id": CosmosField(
            "Candidate.Id",
            "GUID string",
            "Candidate user identifier",
        ),
        "Candidate.FirstName": CosmosField(
            "Candidate.FirstName",
            "string",
            "Candidate first name",
        ),
        "Candidate.LastName": CosmosField(
            "Candidate.LastName",
            "string",
            "Candidate last name",
        ),
        "Exam.ExamId": CosmosField(
            "Exam.ExamId",
            "GUID string",
            "Exam definition identifier",
        ),
        "Exam.ExamName": CosmosField(
            "Exam.ExamName",
            "string",
            "Human-readable exam name",
        ),
        "Exam.ClientCode": CosmosField(
            "Exam.ClientCode",
            "string",
            "Client organization code",
        ),
        "Exam.ClientName": CosmosField(
            "Exam.ClientName",
            "string",
            "Client organization name",
        ),
        "Exam.DeliveryMode": CosmosField(
            "Exam.DeliveryMode",
            "string enum",
            "Exam delivery mode (Online, OnSite, Remote, etc.)",
        ),
        "Exam.StartTimeLocal": CosmosField(
            "Exam.StartTimeLocal",
            "ISO 8601 datetime",
            "Exam start time in candidate's local timezone",
        ),
        "Exam.StopTimeLocal": CosmosField(
            "Exam.StopTimeLocal",
            "ISO 8601 datetime",
            "Exam stop time in candidate's local timezone",
        ),
        "ExamDisconnectedTimes": CosmosField(
            "ExamDisconnectedTimes",
            "array of ISO 8601 datetimes",
            "Record of when candidate disconnected during exam",
        ),
        "RelaunchCount": CosmosField(
            "RelaunchCount",
            "integer",
            "Number of times candidate relaunched exam",
        ),
        "CreatedDate": CosmosField(
            "CreatedDate",
            "ISO 8601 datetime",
            "When the session record was created",
        ),
    },
    query_patterns=[
        "SELECT c.Id, c.ConfirmationCode, c.Status, c.TestStatus FROM c WHERE c.ConfirmationCode = @code",
        "SELECT c.Id, c.Exam.ClientCode, c.Exam.ClientName FROM c WHERE c.Exam.ClientName = @clientName",
        "SELECT c.Id, c.RelaunchCount FROM c WHERE c.RelaunchCount > 0",
        "SELECT c.Id, c.TestStatus FROM c WHERE c.TestStatus = 'Completed'",
        "SELECT c.ConfirmationCode, c.Status, c.TestStatus FROM c WHERE (NOT IS_DEFINED(c.Status) OR LOWER(c.Status) != 'completed') AND (NOT IS_DEFINED(c.TestStatus) OR LOWER(c.TestStatus) != 'completed')",
    ],
    common_filters={
        "ConfirmationCode": "1234567890123456",
        "Exam.ClientCode": "CLIENT123",
        "TestStatus": "Completed",
        "Status": "InProgress",
    },
)

SESSION_LOG_CONTAINER = CosmosContainer(
    database="ExamSession",
    container="session-log",
    purpose="Session lifecycle events (disconnects, relaunches, status changes) stored as Entries array",
    key_fields=["id", "ExamSessionId"],
    fields={
        "ExamSessionId": CosmosField(
            "ExamSessionId",
            "GUID string",
            "Reference to exam-session container Id",
        ),
        "Entries": CosmosField(
            "Entries",
            "array of objects",
            "Array of lifecycle events. Each entry has: Type, SessionLogType (int), Identity, Role, Timestamp, Metadata",
        ),
        "Entries[*].Type": CosmosField(
            "Entries[*].Type",
            "string enum",
            "Event type (SessionStarted, SessionCompleted, Disconnected, Relaunched, etc.)",
        ),
        "Entries[*].SessionLogType": CosmosField(
            "Entries[*].SessionLogType",
            "integer 0-14",
            "Code: 0=Info, 2=KeyCombo, 7=Disconnect, 8=Disconnect-NoRelaunch, 9=BrowserClosed, 11=SessionCompleted, 13=LockdownViolation, 14=SecurityViolation",
            examples=["0", "2", "7", "8", "11"],
        ),
        "Entries[*].Identity": CosmosField(
            "Entries[*].Identity",
            "string",
            "User record identifier (candidate or proctor ID)",
        ),
        "Entries[*].Role": CosmosField(
            "Entries[*].Role",
            "string enum",
            "Actor role (Candidate, Proctor, System)",
        ),
        "Entries[*].Timestamp": CosmosField(
            "Entries[*].Timestamp",
            "ISO 8601 datetime",
            "When the event occurred",
        ),
        "Entries[*].Metadata": CosmosField(
            "Entries[*].Metadata",
            "object",
            "Event-specific metadata (varies by Type and SessionLogType)",
        ),
    },
    query_patterns=[
        "SELECT c.ExamSessionId, e.Type, e.SessionLogType, e.Timestamp FROM c JOIN e IN c.Entries WHERE c.ExamSessionId = @sessionId ORDER BY e.Timestamp",
        "SELECT c.ExamSessionId FROM c WHERE ARRAY_CONTAINS(c.Entries, {Type: 'Disconnected'}, true)",
        "SELECT TOP 5 * FROM c WHERE ARRAY_LENGTH(c.Entries) > 0",
    ],
    common_filters={
        "Entries[*].Type": "Disconnected",
        "Entries[*].SessionLogType": "7",
    },
)

CHAT_CONTAINER = CosmosContainer(
    database="ExamChat",
    container="exam-chat",
    purpose="Chat messages between candidate and proctor",
    key_fields=["id", "ExamSessionId"],
    fields={
        "ExamSessionId": CosmosField(
            "ExamSessionId",
            "GUID string",
            "Reference to exam session",
        ),
        "FromUserId": CosmosField(
            "FromUserId",
            "GUID string",
            "Sender user ID (candidate or proctor)",
        ),
        "FromUserRole": CosmosField(
            "FromUserRole",
            "string enum",
            "Sender role (Candidate, Proctor)",
        ),
        "ToUserId": CosmosField(
            "ToUserId",
            "GUID string",
            "Recipient user ID",
        ),
        "Message": CosmosField(
            "Message",
            "string",
            "Chat message content",
        ),
        "SentTimestamp": CosmosField(
            "SentTimestamp",
            "ISO 8601 datetime",
            "When message was sent",
        ),
    },
    query_patterns=[
        "SELECT c.Message, c.FromUserRole, c.SentTimestamp FROM c WHERE c.ExamSessionId = @sessionId ORDER BY c.SentTimestamp",
    ],
    common_filters={
        "FromUserRole": "Proctor",
        "ToUserId": "<candidate-id>",
    },
)

CONFERENCE_CONTAINER = CosmosContainer(
    database="PPR.Conferences",
    container="conference",
    purpose="Video conference/Twilio room data for proctored sessions",
    key_fields=["id", "ExamSessionId"],
    fields={
        "ExamSessionId": CosmosField(
            "ExamSessionId",
            "GUID string",
            "Reference to exam session",
        ),
        "RoomName": CosmosField(
            "RoomName",
            "string",
            "Twilio Room name/identifier",
        ),
        "RoomStatus": CosmosField(
            "RoomStatus",
            "string enum",
            "Room status (Active, Completed, Failed, etc.)",
        ),
        "StartTime": CosmosField(
            "StartTime",
            "ISO 8601 datetime",
            "When room was created",
        ),
        "EndTime": CosmosField(
            "EndTime",
            "ISO 8601 datetime | null",
            "When room ended (null if still active)",
        ),
        "Participants": CosmosField(
            "Participants",
            "array",
            "Array of participant records (candidate, proctor, observer)",
        ),
    },
    query_patterns=[
        "SELECT c.RoomName, c.RoomStatus, c.StartTime FROM c WHERE c.ExamSessionId = @sessionId",
    ],
    common_filters={
        "RoomStatus": "Completed",
    },
)

ASSIGNMENT_CONTAINER = CosmosContainer(
    database="Assignment",
    container="assignment",
    purpose="Proctor assignment and task data",
    key_fields=["id", "ExamSessionId"],
    fields={
        "ExamSessionId": CosmosField(
            "ExamSessionId",
            "GUID string",
            "Reference to exam session",
        ),
        "ProctoredBy": CosmosField(
            "ProctoredBy",
            "GUID string",
            "Proctor user ID",
        ),
        "AssignmentStatus": CosmosField(
            "AssignmentStatus",
            "string enum",
            "Status (Assigned, Active, Completed, Cancelled)",
        ),
        "CreatedDate": CosmosField(
            "CreatedDate",
            "ISO 8601 datetime",
            "When assignment was created",
        ),
    },
    query_patterns=[
        "SELECT c.ExamSessionId, c.ProctoredBy, c.AssignmentStatus FROM c WHERE c.ExamSessionId = @sessionId",
    ],
    common_filters={
        "AssignmentStatus": "Active",
    },
)

# Container registry for easy lookup
COSMOS_CONTAINERS: Dict[tuple[str, str], CosmosContainer] = {
    ("ExamSession", "exam-session"): EXAM_SESSION_CONTAINER,
    ("ExamSession", "session-log"): SESSION_LOG_CONTAINER,
    ("ExamChat", "exam-chat"): CHAT_CONTAINER,
    ("PPR.Conferences", "conference"): CONFERENCE_CONTAINER,
    ("Assignment", "assignment"): ASSIGNMENT_CONTAINER,
}

# =============================================================================
# LOG ANALYTICS (KQL) SCHEMA
# =============================================================================

KQL_WORKSPACE_INFO = {
    "proproctor": {
        "purpose": "Application-level logs from ProProctor services",
        "tables": ["AppTraces", "AppExceptions", "AppRequests", "AppEvents"],
        "key_fields": {
            "AppTraces": ["TimeGenerated", "Message", "SeverityLevel", "Properties"],
            "AppExceptions": ["TimeGenerated", "ExceptionType", "ExceptionMessage", "Properties"],
            "AppRequests": ["TimeGenerated", "Name", "ResultCode", "Duration"],
            "AppEvents": ["TimeGenerated", "Name", "Properties"],
        },
        "source_services": [
            "app-proproctor-exam-sessions-api",
            "app-proproctor-candidate-app-uat",
            "app-proproctor-assignments-api",
            "app-proproctor-chat-api",
            "app-proproctor-conferences-api",
        ],
    },
    "infrastructure": {
        "purpose": "Kubernetes and container-level infrastructure logs",
        "tables": ["KubeEvents", "ContainerLogV2", "KubePodInventory", "KubeNodeInventory"],
        "key_fields": {
            "KubeEvents": ["TimeGenerated", "Name", "Reason", "Message"],
            "ContainerLogV2": ["TimeGenerated", "ContainerName", "LogMessage"],
            "KubePodInventory": ["TimeGenerated", "Name", "PodStatus", "ContainerStatusReason"],
            "KubeNodeInventory": ["TimeGenerated", "Computer", "Condition"],
        },
        "common_reasons": [
            "Failed",
            "BackOff",
            "Unhealthy",
            "OOMKilling",
            "CrashLoopBackOff",
        ],
    },
}

# =============================================================================
# SCHEMA QUERY HINTS
# =============================================================================

SCHEMA_QUERY_PATTERNS = {
    "find_session_by_code": {
        "database": "ExamSession",
        "container": "exam-session",
        "query": "SELECT TOP 1 * FROM c WHERE c.ConfirmationCode = @code ORDER BY c.CreatedDate DESC",
        "note": "Use to resolve confirmation code to ExamSessionId",
    },
    "find_disconnects": {
        "database": "ExamSession",
        "container": "session-log",
        "query": "SELECT c.ExamSessionId, e.Timestamp FROM c JOIN e IN c.Entries WHERE e.SessionLogType IN (7, 8) ORDER BY e.Timestamp",
        "note": "SessionLogType 7=Disconnect, 8=Disconnect-NoRelaunch",
    },
    "find_test_completed": {
        "database": "ExamSession",
        "container": "exam-session",
        "query": "SELECT c.ConfirmationCode, c.Exam.StartTimeLocal FROM c WHERE c.TestStatus = 'Completed'",
        "note": "Find all sessions where exam completed",
    },
    "check_applied_blocks": {
        "database": "ExamSession",
        "container": "session-log",
        "query": "SELECT c.ExamSessionId, e.SessionLogType FROM c JOIN e IN c.Entries WHERE e.SessionLogType IN (13, 14)",
        "note": "SessionLogType 13=LockdownViolation, 14=SecurityViolation",
    },
}

# =============================================================================
# SCHEMA GUIDANCE FOR AGENT
# =============================================================================

def get_container_info(database: str, container: str) -> CosmosContainer | None:
    """Get schema information for a Cosmos container."""
    return COSMOS_CONTAINERS.get((database, container))


def get_all_valid_containers() -> List[CosmosContainer]:
    """Get all registered Cosmos containers."""
    return list(COSMOS_CONTAINERS.values())


def get_query_hint(hint_name: str) -> Dict | None:
    """Get a pre-built query pattern hint."""
    return SCHEMA_QUERY_PATTERNS.get(hint_name)


def schema_summary_for_prompt() -> str:
    """Generate a summary of schema info for injection into system prompt."""
    summary = "## Database Schema Summary\n\n"

    for container in get_all_valid_containers():
        summary += f"### {container.database}/{container.container}\n"
        summary += f"**Purpose:** {container.purpose}\n"
        summary += f"**Key Fields:** {', '.join(container.key_fields)}\n"
        summary += f"**Common Filters:** "
        summary += (
            ", ".join([f"{k}={v}" for k, v in container.common_filters.items()])
            if container.common_filters
            else "None"
        )
        summary += "\n\n"

    return summary


def field_info_for_container(database: str, container: str) -> str | None:
    """Get detailed field information for a container."""
    container_obj = get_container_info(database, container)
    if not container_obj:
        return None

    info = f"## Fields in {database}/{container}\n\n"
    for field_name, field in container_obj.fields.items():
        info += f"- **{field_name}** ({field.type_hint}): {field.description}\n"
    return info
