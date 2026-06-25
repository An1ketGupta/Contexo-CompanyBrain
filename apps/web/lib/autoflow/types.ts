export type TriggerType =
  | "document_uploaded"
  | "document_ready"
  | "document_failed"
  | "query_no_results"
  | "message_feedback_negative"
  | "scheduled"
  | "employee_joined"
  | "knowledge_gap_detected"
  | "approval_requested"
  | "agent_completed"
  | "compliance_acknowledged";

export type ActionType =
  | "generate_output"
  | "send_email"
  | "post_slack"
  | "create_notion_page"
  | "notify_admin"
  | "emit_webhook"
  | "create_task"
  | "hold_for_approval";

export interface TriggerConfig {
  cron?: string;
  filters?: Record<string, unknown>;
}

export interface ActionStep {
  id: string;
  type: ActionType;
  config: Record<string, unknown>;
  order: number;
}

export interface AutoflowDraft {
  name: string;
  description: string | null;
  trigger_type: TriggerType;
  trigger_config: TriggerConfig;
  actions: ActionStep[];
  confidence_threshold: number | null;
  is_active: boolean;
}

export interface AutoflowRow extends AutoflowDraft {
  id: string;
  org_id: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  last_fired_at: string | null;
}

export interface AutoflowRunStep {
  index: number;
  type: string;
  status: "pending" | "running" | "completed" | "failed" | "skipped" | "held_for_approval";
  started_at?: string;
  completed_at?: string;
  error?: string;
  output?: Record<string, unknown>;
}

export interface AutoflowRun {
  id: string;
  autoflow_id: string;
  status: "pending" | "running" | "completed" | "failed" | "held_for_approval" | "cancelled";
  steps: AutoflowRunStep[];
  steps_completed: number;
  total_steps: number;
  error_message: string | null;
  blocking_approval_id: string | null;
  started_at: string;
  completed_at: string | null;
  trigger_payload: Record<string, unknown>;
}

export interface FieldSchema {
  path: string;
  label: string;
  description?: string;
  example?: string;
}
