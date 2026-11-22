export type UserRole = "USER" | "ADMIN";
export type DataType = "TRIAL_BALANCE" | "JOURNAL";
export type TransformationStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
export type ProcessingStage = "QUEUED" | "PROFILING" | "PLANNING" | "TRANSFORMING" | "VALIDATING" | "EXPORTING" | "COMPLETED" | "FAILED" | "CANCELLED";

export interface User { id: string; name: string; email: string; role: UserRole }
export interface TargetColumn { key: string; label: string; kind: "text" | "number" | "date"; required: boolean }
export interface TemplateInfo { id: string; data_type: DataType; name: string; description: string; columns: TargetColumn[] }
export interface DocumentProfile { id: string; name: string; data_type: DataType | null; available: boolean; memory_enabled: boolean; template_ids: string[]; schema_version: number }
export interface MemoryCard {
  id: string; revision: number; document_type: string; template_id: string; title: string;
  description: string; header: string[][]; column_mapping: Record<string, number>;
  row_mode: "flat" | "account_tree"; verified_rows: number; independent_totals: boolean;
}
export interface MemoryRecord {
  card: MemoryCard; owner_id: string; status: "draft" | "approved" | "disabled";
  scope: "private" | "shared"; successes: number; rejections: number;
}
export interface MemoryCase {
  id: string; transformation_id: string; outcome: "resolved" | "failed" | "unsupported";
  reason: string; card_id: string | null; created_at: string;
}

export interface LLMGenerationOptions {
  temperature: number;
  max_tokens: number;
  context_size: number | null;
}
export interface LLMProfile {
  enabled: boolean;
  protocol: "ollama" | "openai";
  base_url: string;
  model: string;
  timeout_seconds: number;
  connect_timeout_seconds: number;
  thinking: boolean;
  no_think_suffix: boolean;
  keep_alive: string | null;
  api_key_env: string | null;
  structured_output: boolean;
  generation: LLMGenerationOptions;
}
export interface LLMConfiguration {
  active_profile: string;
  fallback_profiles: string[];
  profiles: Record<string, LLMProfile>;
}
export interface LLMSettingsResponse {
  configuration: LLMConfiguration;
  selected_profile: string;
  environment_override: string | null;
}
export interface LLMProbeResult {
  profile_name: string;
  server_available: boolean;
  model_available: boolean;
  available: boolean;
  latency_ms: number;
  status_code: number | null;
  message: string;
  models: string[];
}
export interface TransformationSummary {
  id: string;
  source_filename: string;
  data_type: DataType;
  template_id: string;
  status: TransformationStatus;
  current_stage: ProcessingStage;
  processed_rows: number;
  unprocessed_rows: number;
  control_status: "NOT_PERFORMED" | "PASSED" | "WARNINGS" | "FAILED";
  version: number;
  created_at: string;
  updated_at: string;
}

export interface TransformationDetail extends TransformationSummary {
  warnings: string[];
  error_message?: string;
  controls: {
    calculated_sums?: Record<string, number>;
    control_comparisons?: Record<string, { declared: number; calculated: number; difference: number; passed: boolean }>;
    missing_required?: Record<string, number>;
  };
  llm_used: boolean;
}

export interface PreviewResponse { columns: TargetColumn[]; rows: Array<Record<string, unknown>>; total: number; version: number }
export interface SourcePreview {
  upload_id: string;
  filename: string;
  sheets: string[];
  sheet_name: string;
  columns: string[];
  rows: Array<{ row: number; cells: Array<string | number | boolean | null> }>;
  total_rows: number;
  total_columns: number;
  columns_truncated: boolean;
}
export interface TransformationRun {
  id: string;
  transformation_id: string;
  target_version: number;
  feedback?: string | null;
  status: TransformationStatus;
  started_at: string;
  finished_at?: string | null;
}
export interface TransformationRunEvent {
  id: number;
  stage: ProcessingStage;
  event_type: string;
  level: "INFO" | "WARNING" | "ERROR";
  details: Record<string, unknown>;
  created_at: string;
}
export interface TransformationMemo { run: TransformationRun; events: TransformationRunEvent[] }
export interface AuditLogItem {
  id: number;
  user_id?: string;
  user_name?: string;
  user_email?: string;
  action: string;
  entity_type?: string;
  entity_id?: string;
  outcome: string;
  details: Record<string, unknown>;
  created_at: string;
}
