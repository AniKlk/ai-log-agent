export interface Finding {
  description: string;
  severity: 'critical' | 'warning' | 'info';
  evidence: string[];
}

export interface TimelineEntry {
  timestamp: string | null;
  event: string;
  severity: 'critical' | 'warning' | 'info' | null;
}

export interface SourceSummary {
  app_insights_events: number;
  infra_events: number;
  cosmos_session_records: number;
  cosmos_session_log_records: number;
  cosmos_conference_records: number;
  cosmos_assignment_records: number;
}

export interface AppInsightsSummary {
  total_events: number;
  info_events: number;
  error_events: number;
  disconnect_events: number;
  marker_events: number;
  non_marker_events: number;
  error_records: number;
  top_error_signatures: string[];
}

export interface AppInsightsLogRow {
  timestamp: string | null;
  type: 'info' | 'error' | 'disconnect';
  message: string;
}

export interface AgentOutput {
  summary: string;
  confirmation_codes?: string[];
  download_links?: Record<string, string>;
  per_confirmation_code_summaries?: Record<string, string>;
  key_findings: Finding[];
  root_cause: string | null;
  root_cause_confidence: 'confirmed' | 'probable' | 'uncertain' | null;
  timeline: TimelineEntry[];
  app_insights_summary?: AppInsightsSummary | null;
  app_insights_logs?: AppInsightsLogRow[];
  source_summary?: SourceSummary | null;
  per_confirmation_code_source_summary?: Record<string, SourceSummary>;
  tools_invoked: string[];
  warnings: string[] | null;
}

export interface AnalyzeResponse {
  answer: AgentOutput;
  request_id: string;
  duration_ms: number;
}

export interface ConversationMessage {
  role: 'user' | 'assistant';
  content: string;
}
