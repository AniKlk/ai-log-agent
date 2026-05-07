"""
Diagnostic tools for debugging common query patterns that return 0 results.

Issues like "completed exams for client X between date Y and Z" often fail due to:
- Date format mismatches (ISO 8601 normalization)
- Case-sensitive field name mismatches
- Overly restrictive filters hidden in compound WHERE clauses
- Status field variance (some records may have Status vs TestStatus)

This module provides diagnostic query builders to systematically check each component.
"""

from datetime import datetime, timezone
from typing import Dict, List, Tuple


def normalize_iso_date(date_input: str | datetime) -> str:
    """Normalize a date to ISO 8601 UTC format."""
    if isinstance(date_input, datetime):
        return date_input.astimezone(timezone.utc).isoformat()
    
    # Try parsing common formats
    for fmt in [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ]:
        try:
            dt = datetime.strptime(date_input, fmt)
            dt_utc = dt.replace(tzinfo=timezone.utc)
            return dt_utc.isoformat()
        except ValueError:
            continue
    
    # If no parse worked, return as-is (might be ISO already)
    return date_input


class CompletedExamQueryDiagnostic:
    """
    Diagnostic query builder for: "How many candidates completed exam between X and Y for client Z?"
    
    Systematically checks:
    1. Data exists for time range (any status)
    2. Exam records exist for the client
    3. Completed status records exist (both Status and TestStatus variants)
    4. Combined filter returns results
    """

    @staticmethod
    def query_any_sessions_in_range(start_date: str, end_date: str) -> Dict:
        """Check: Do ANY sessions exist in this date range?"""
        start_iso = normalize_iso_date(start_date)
        end_iso = normalize_iso_date(end_date)

        return {
            "name": "check_any_sessions",
            "purpose": "Verify date range has data at all",
            "database": "ExamSession",
            "container": "exam-session",
            "query": (
                f"SELECT TOP 100 c.ConfirmationCode, c.Status, c.CreatedDate "
                f"FROM c "
                f"WHERE c.CreatedDate >= '{start_iso}' "
                f"AND c.CreatedDate <= '{end_iso}' "
                f"ORDER BY c.CreatedDate DESC"
            ),
        }

    @staticmethod
    def query_sessions_by_client(client_name: str) -> Dict:
        """Check: Do sessions exist for this client name (any date)?"""
        return {
            "name": "check_client_sessions",
            "purpose": "Verify client name exists in records",
            "database": "ExamSession",
            "container": "exam-session",
            "query": (
                f"SELECT TOP 100 c.ConfirmationCode, c.Exam.ClientName, c.CreatedDate "
                f"FROM c "
                f"WHERE c.Exam.ClientName = '{client_name}' "
                f"ORDER BY c.CreatedDate DESC"
            ),
        }

    @staticmethod
    def query_completed_status_field(start_date: str, end_date: str) -> Dict:
        """Check: How many have Status='Completed' in date range? (case-insensitive)"""
        start_iso = normalize_iso_date(start_date)
        end_iso = normalize_iso_date(end_date)

        return {
            "name": "check_status_completed",
            "purpose": "Check Status field for 'Completed' (ignoring case)",
            "database": "ExamSession",
            "container": "exam-session",
            "query": (
                f"SELECT TOP 100 c.ConfirmationCode, c.Status, c.CreatedDate "
                f"FROM c "
                f"WHERE LOWER(c.Status) = 'completed' "
                f"AND c.CreatedDate >= '{start_iso}' "
                f"AND c.CreatedDate <= '{end_iso}' "
                f"ORDER BY c.CreatedDate DESC"
            ),
        }

    @staticmethod
    def query_teststatus_completed(start_date: str, end_date: str) -> Dict:
        """Check: How many have TestStatus='Completed' in date range? (case-insensitive)"""
        start_iso = normalize_iso_date(start_date)
        end_iso = normalize_iso_date(end_date)

        return {
            "name": "check_teststatus_completed",
            "purpose": "Check TestStatus field for 'Completed' (ignoring case)",
            "database": "ExamSession",
            "container": "exam-session",
            "query": (
                f"SELECT TOP 100 c.ConfirmationCode, c.TestStatus, c.CreatedDate "
                f"FROM c "
                f"WHERE LOWER(c.TestStatus) = 'completed' "
                f"AND c.CreatedDate >= '{start_iso}' "
                f"AND c.CreatedDate <= '{end_iso}' "
                f"ORDER BY c.CreatedDate DESC"
            ),
        }

    @staticmethod
    def query_completed_by_client_date(
        client_name: str, start_date: str, end_date: str
    ) -> Dict:
        """
        Check: Completed sessions for this client in this date range.
        
        Uses BOTH Status and TestStatus with OR to catch all 'completed' variants.
        """
        start_iso = normalize_iso_date(start_date)
        end_iso = normalize_iso_date(end_date)

        return {
            "name": "check_completed_by_client_and_date",
            "purpose": "Main query: completed exams for client in date range",
            "database": "ExamSession",
            "container": "exam-session",
            "query": (
                f"SELECT TOP 500 c.ConfirmationCode, c.Status, c.TestStatus, "
                f"c.Exam.ClientName, c.Exam.StartTimeLocal, c.CreatedDate "
                f"FROM c "
                f"WHERE c.Exam.ClientName = '{client_name}' "
                f"AND c.CreatedDate >= '{start_iso}' "
                f"AND c.CreatedDate <= '{end_iso}' "
                f"AND (LOWER(c.Status) = 'completed' OR LOWER(c.TestStatus) = 'completed') "
                f"ORDER BY c.CreatedDate DESC"
            ),
        }

    @staticmethod
    def query_completed_count_by_client_date(
        client_name: str, start_date: str, end_date: str
    ) -> Dict:
        """
        Check: COUNT of completed sessions.
        
        Note: This uses TOP + projection instead of GROUP BY
        (Cosmos has limitations with GROUP BY aggregates).
        """
        start_iso = normalize_iso_date(start_date)
        end_iso = normalize_iso_date(end_date)

        return {
            "name": "count_completed_by_client_and_date",
            "purpose": "Count completed exams (using projection, not GROUP BY)",
            "database": "ExamSession",
            "container": "exam-session",
            "query": (
                f"SELECT TOP 500 c.ConfirmationCode, c.Status, c.TestStatus "
                f"FROM c "
                f"WHERE c.Exam.ClientName = '{client_name}' "
                f"AND c.CreatedDate >= '{start_iso}' "
                f"AND c.CreatedDate <= '{end_iso}' "
                f"AND (LOWER(c.Status) = 'completed' OR LOWER(c.TestStatus) = 'completed')"
            ),
        }

    @staticmethod
    def build_diagnostic_sequence(
        client_name: str, start_date: str, end_date: str
    ) -> List[Dict]:
        """
        Build a sequence of diagnostic queries to run in order.
        
        Each query is progressively more filtered so you can see where data disappears.
        """
        return [
            CompletedExamQueryDiagnostic.query_any_sessions_in_range(start_date, end_date),
            CompletedExamQueryDiagnostic.query_sessions_by_client(client_name),
            CompletedExamQueryDiagnostic.query_completed_status_field(start_date, end_date),
            CompletedExamQueryDiagnostic.query_teststatus_completed(start_date, end_date),
            CompletedExamQueryDiagnostic.query_completed_by_client_date(
                client_name, start_date, end_date
            ),
        ]

    @staticmethod
    def print_diagnostic_queries(
        client_name: str, start_date: str, end_date: str
    ) -> str:
        """Generate a human-readable diagnostic report."""
        queries = CompletedExamQueryDiagnostic.build_diagnostic_sequence(
            client_name, start_date, end_date
        )

        output = [
            "=" * 80,
            "DIAGNOSTIC QUERY SEQUENCE",
            f"Client: {client_name}",
            f"Date Range: {start_date} to {end_date}",
            "=" * 80,
            "",
        ]

        for i, q in enumerate(queries, 1):
            output.append(f"Step {i}: {q['name']}")
            output.append(f"Purpose: {q['purpose']}")
            output.append(f"Database: {q['database']} / {q['container']}")
            output.append(f"Query: {q['query']}")
            output.append("")

        return "\n".join(output)


# Example usage
if __name__ == "__main__":
    diagnostic = CompletedExamQueryDiagnostic()
    report = diagnostic.print_diagnostic_queries(
        client_name="LSAC",
        start_date="2026-04-01",
        end_date="2026-05-06",
    )
    print(report)
