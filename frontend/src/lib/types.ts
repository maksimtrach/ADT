export type UserRole = "USER" | "ADMIN";
export type DataType = "TRIAL_BALANCE" | "JOURNAL";
export type TransformationStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
export type ProcessingStage = "QUEUED" | "PROFILING" | "PLANNING" | "TRANSFORMING" | "VALIDATING" | "EXPORTING" | "COMPLETED" | "FAILED";

export interface User { id: string; name: string; email: string; role: UserRole }
export interface TargetColumn { key: string; label: string; kind: "text" | "number" | "date"; required: boolean }
export interface TemplateInfo { id: string; data_type: DataType; name: string; description: string; columns: TargetColumn[] }

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
