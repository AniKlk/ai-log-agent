"""
Lightweight hypothesis tracking for the investigation loop.

Each tool result sharpens or contradicts the working hypothesis.
The orchestrator injects a hypothesis-state message mid-run so the
LLM re-anchors on what has been confirmed vs. ruled out.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class HypothesisState:
    """Accumulates evidence for and against a provisional root cause."""

    candidate_side_evidence: list[str] = field(default_factory=list)
    backend_evidence: list[str] = field(default_factory=list)
    infra_evidence: list[str] = field(default_factory=list)
    ruled_out: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    # Internal bookkeeping
    _tool_calls: int = field(default=0, repr=False)

    def record_tool(
        self,
        tool_name: str,
        result_json: dict,
    ) -> None:
        self._tool_calls += 1

        source_summary = result_json.get("source_summary") or {}
        events: list[dict] = result_json.get("events") or []
        rows: list[dict] = result_json.get("rows") or []
        errors: list[dict] = result_json.get("errors") or []

        if tool_name == "getSessionData":
            ai_events = int(source_summary.get("app_insights_events", 0))
            infra_ev = int(source_summary.get("infra_events", 0))
            cosmos_sl = int(source_summary.get("cosmos_session_log_records", 0))

            if ai_events == 0 and infra_ev == 0 and cosmos_sl == 0:
                self.open_questions.append(
                    "All data sources returned empty for this session — confirm code "
                    "and workspace coverage before concluding absence."
                )
            for ev in events:
                msg = str(ev.get("message", "")).lower()
                src = str(ev.get("source", ""))
                if "candidate-app exit marker" in msg or "exiting" in msg:
                    self.candidate_side_evidence.append(
                        f"App Insights exit signal @{ev.get('timestamp','?')}: {ev.get('message','')[:120]}"
                    )
                elif "backend-check-summary" in msg:
                    self.backend_evidence.append(
                        f"Backend check summary present: {ev.get('message','')[:200]}"
                    )
                elif "oomkill" in msg or "crashloop" in msg or "evicted" in msg:
                    self.infra_evidence.append(
                        f"Infra pressure @{ev.get('timestamp','?')}: {ev.get('message','')[:120]}"
                    )
            if errors:
                self.backend_evidence.append(
                    f"{len(errors)} error event(s) in session data."
                )

        elif tool_name == "queryKQL":
            workspace = ""
            for row in rows[:5]:
                row_str = str(row).lower()
                if "oomkill" in row_str or "fail" in row_str or "evict" in row_str:
                    self.infra_evidence.append(
                        f"KQL ({workspace}): infra signal — {str(row)[:120]}"
                    )
                elif "timeout" in row_str or "5xx" in row_str or "503" in row_str:
                    self.backend_evidence.append(
                        f"KQL ({workspace}): backend error — {str(row)[:120]}"
                    )
            if not rows:
                workspace = result_json.get("workspace", "unknown")
                self.ruled_out.append(
                    f"No evidence found in KQL workspace '{workspace}'."
                )

        elif tool_name == "queryCosmos":
            container = result_json.get("container", "?")
            if not rows:
                self.ruled_out.append(
                    f"Cosmos container '{container}' returned no rows."
                )

        elif tool_name == "getSessionLogStats":
            hits = int(result_json.get("candidates_with_hits", 0))
            total_window = int(result_json.get("active_client_sessions_in_window", 0))
            if hits > 0 and total_window > 0:
                pct = round(hits / total_window * 100, 1)
                self.candidate_side_evidence.append(
                    f"getSessionLogStats: {hits}/{total_window} candidates affected ({pct}%)."
                )
            elif hits == 0:
                self.ruled_out.append(
                    "getSessionLogStats: zero candidates affected in the search window."
                )

    def should_inject(self, iteration: int) -> bool:
        """Inject hypothesis message at iteration 3 and every 3 iterations after."""
        return self._tool_calls >= 3 and (self._tool_calls % 3 == 0)

    def to_system_message(self) -> str | None:
        if not any([
            self.candidate_side_evidence,
            self.backend_evidence,
            self.infra_evidence,
            self.ruled_out,
            self.open_questions,
        ]):
            return None

        lines = ["Working hypothesis state (accumulated from tool evidence so far):"]
        if self.candidate_side_evidence:
            lines.append(
                "  Candidate-side evidence:\n"
                + "\n".join(f"    • {e}" for e in self.candidate_side_evidence[-4:])
            )
        if self.backend_evidence:
            lines.append(
                "  Backend/service evidence:\n"
                + "\n".join(f"    • {e}" for e in self.backend_evidence[-4:])
            )
        if self.infra_evidence:
            lines.append(
                "  Infrastructure evidence:\n"
                + "\n".join(f"    • {e}" for e in self.infra_evidence[-4:])
            )
        if self.ruled_out:
            lines.append(
                "  Ruled out (no evidence found):\n"
                + "\n".join(f"    • {e}" for e in self.ruled_out[-4:])
            )
        if self.open_questions:
            lines.append(
                "  Open questions (investigate before finalising):\n"
                + "\n".join(f"    • {q}" for q in self.open_questions[-3:])
            )
        lines.append(
            "Anchor your root_cause and conclusions to the above evidence. "
            "Do not contradict confirmed findings. Do not add caveats unsupported by evidence."
        )
        return "\n".join(lines)
