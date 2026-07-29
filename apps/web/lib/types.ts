export type Json = string | number | boolean | null | { [key: string]: Json } | Json[];

export type OrgPlan = "free" | "starter" | "growth" | "business";
export type UserRole = "admin" | "member";
export type DocumentStatus = "pending" | "processing" | "ready" | "failed";
export type DocumentFileType =
  | "pdf"
  | "docx"
  | "txt"
  | "md"
  | "xlsx"
  | "pptx"
  | "html"
  | "csv"
  | "vtt"
  | "teams_transcript"
  | "transcript";
export type DocumentVisibility = "private" | "org";
export type MessageRole = "user" | "assistant";
export type MessageFeedback = "positive" | "negative";

export interface Organization {
  id: string;
  name: string;
  slug: string;
  plan: OrgPlan;
  created_at: string;
}

export interface User {
  id: string;
  org_id: string;
  role: UserRole;
  display_name: string | null;
  created_at: string;
}

export type DocumentHealthLabel =
  | "healthy"
  | "stale"
  | "at_risk"
  | "unused";

export interface Document {
  id: string;
  org_id: string;
  name: string;
  file_path: string;
  file_type: DocumentFileType;
  file_size_bytes: number | null;
  status: DocumentStatus;
  chunk_count: number | null;
  metadata: Json;
  created_by: string | null;
  created_at: string;
  tags?: string[];
  // Source integration ('zoom' | 'google_meet_transcript' | 'upload' | …)
  // and the migration-084/086 privacy gate — private docs are owner-only.
  source?: string | null;
  visibility?: DocumentVisibility;
  // V4 #34
  health_score?: number | null;
  health_label?: DocumentHealthLabel | null;
  last_accessed_at?: string | null;
  // V2 Day 13 / #38
  review_frequency_days?: number | null;
  review_due_at?: string | null;
  last_reviewed_at?: string | null;
  current_version_id?: string | null;
  current_version_number?: number | null;
  current_version_uploaded_at?: string | null;
  version_count?: number | null;
}

export interface DocumentTag {
  tag: string;
  count: number;
}

export interface UsageSnapshot {
  plan: string;
  used: number;
  limit: number | null;
  reset_at: string;
  seconds_until_reset: number;
  unlimited: boolean;
  source: "redis" | "local";
}

export interface Chunk {
  id: string;
  org_id: string;
  document_id: string;
  content: string;
  chunk_index: number;
  page_number: number | null;
  section_heading: string | null;
  token_count: number | null;
  metadata: Json;
}

export interface Conversation {
  id: string;
  org_id: string;
  user_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

// Mirrors the dict shape produced by app/services/llm/task_chain.py:_dedupe_sources.
export interface MessageSource {
  chunk_id: string;
  document_id: string | null;
  document_name: string;
  document_version_id?: string | null;
  version_number?: number | null;
  page_number: number | null;
  section_heading: string | null;
  excerpt: string;
  snippet: string | null;
  review_due_at?: string | null;
}

export type ConfidenceLevel = "high" | "medium" | "low";

export interface MessageConfidence {
  level: ConfidenceLevel;
  // 0–10 scale, matching the backend.
  score: number;
  // Number of cited chunks the confidence was averaged over.
  n: number;
}

export type QueryIntent =
  | "factual_qa"
  | "task_generation"
  | "analysis"
  | "search"
  // Production Roadmap 1.9 — Time-boxed Quick Answer. A bounded
  // specialization of factual_qa for short fact lookups; the orchestrator
  // caps tool rounds + search count for sub-2s perceived latency.
  | "quick_answer";

export interface MessageMetadata {
  confidence?: MessageConfidence;
  intent?: QueryIntent;
}

export interface Message {
  id: string;
  conversation_id: string;
  org_id: string;
  role: MessageRole;
  content: string;
  sources: MessageSource[] | null;
  feedback: MessageFeedback | null;
  metadata: MessageMetadata | null;
  created_at: string;
}

// Competitor watchlist hit reported by the post-generation detector.
// Mirrors the row shape the API persists to `competitor_mentions` (term grain).
export interface CompetitorMatch {
  term: string;
  source: "org" | "user";
  count: number;
  snippet: string;
}

// SSE event types emitted by POST /chat/stream and POST /chat/messages/{id}/regenerate.
// Shape mirrors what app/api/routers/chat.py:_event_to_payload sends on the wire.
export type ChatStreamEvent =
  | {
      type: "start";
      conversation_id: string;
      parent_user_message_id?: string;
      branch_index?: number;
    }
  | { type: "intent"; intent: QueryIntent }
  | { type: "searching"; query: string }
  | { type: "searched"; query: string; hit_count: number }
  | { type: "sources"; sources: MessageSource[] }
  | { type: "token"; text: string }
  | { type: "knowledge_gap"; topics: string[] }
  | {
      type: "competitor_warning";
      matches: CompetitorMatch[];
    }
  | {
      type: "confidence";
      level: ConfidenceLevel;
      score: number;
      chunks_considered: number;
    }
  | {
      type: "done";
      message_id: string;
      tool_calls: number;
      parent_user_message_id?: string;
      branch_index?: number;
      total_branches?: number;
    }
  | { type: "error"; message: string }
  // V4 #79 — moderation refusal delivered inline on the SSE stream (HTTP 200).
  // We keep `code: "moderation_blocked"` consistent with the HTTP envelope so
  // the assistant bubble's ErrorPanel renders the amber/shield variant.
  | {
      type: "moderation_block";
      code: "moderation_blocked";
      message: string;
      reason?: string | null;
      request_id?: string;
    };

// ── V3 Day 1: empty-state banner ─────────────────────────────────────────────

export interface DocumentStatusSummary {
  total: number;
  ready: number;
  processing: number;
  failed: number;
  has_ready: boolean;
}

// ── V3 Day 2: prompt template library ────────────────────────────────────────

export type TemplateCategory =
  | "Email"
  | "Job Description"
  | "Announcement"
  | "Policy Q&A"
  | "Meeting Prep"
  | "Customer Response"
  | "Slack Reply"
  | "Other";

export interface TemplateVariable {
  name: string;
  label: string;
  placeholder: string;
  required: boolean;
}

export interface PromptTemplate {
  id: string;
  title: string;
  description: string | null;
  template_text: string;
  category: TemplateCategory;
  is_shared: boolean;
  is_builtin: boolean;
  use_count: number;
  // V4 #70 — {{variable}} definitions.
  variables: TemplateVariable[];
  // Production Roadmap 1.7 — context-template discriminator + payload.
  // When `is_context_template=true`, this row stores a reusable
  // pinned_context preamble; `template_text` is unused for these rows.
  is_context_template: boolean;
  pinned_context: string | null;
  org_id: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

// ── Document generation pipeline ───────────────────────────────────────────
// Templates, their versions, and the typed fields detected inside them.
// Distinct from `PromptTemplate` above, which is a chat prompt: these are
// uploaded .docx/.pdf files that get filled per candidate and emailed out.

export type DocumentDataType =
  | "text"
  | "email"
  | "phone"
  | "currency"
  | "date"
  | "number"
  | "boolean"
  | "address"
  | "country"
  | "state"
  | "city"
  | "designation"
  | "department"
  | "manager"
  | "company"
  | "signature_block"
  | "custom";

export type ReviewStatus = "proposed" | "confirmed" | "rejected";
export type DocTemplateStatus = "draft" | "active" | "archived";
export type AnalysisStatus =
  | "pending"
  | "analyzing"
  | "completed"
  | "failed"
  | "manual";

export interface DocumentType {
  id: string;
  key: string;
  label: string;
  description: string | null;
  is_system: boolean;
}

export interface DocTemplate {
  id: string;
  name: string;
  description: string | null;
  status: DocTemplateStatus;
  is_default: boolean;
  document_type_id: string;
  document_type_key: string | null;
  document_type_label: string | null;
  current_version_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface DocTemplateVersion {
  id: string;
  version_no: number;
  original_filename: string;
  mime_type: string;
  file_bytes: number | null;
  file_sha256: string;
  analysis_status: AnalysisStatus;
  analysis_error: string | null;
  analyzed_at: string | null;
  detected_type_id: string | null;
  detected_type_confidence: number | null;
  created_at: string | null;
}

export interface DocTemplateVariable {
  id: string;
  internal_name: string;
  display_name: string;
  description: string | null;
  data_type: DocumentDataType;
  is_required: boolean;
  default_value: string | null;
  validation_rules: Record<string, unknown>;
  aliases: string[];
  example_value: string | null;
  /** 0–1. Null when a human created the field. */
  confidence: number | null;
  status: ReviewStatus;
  source: "ai" | "manual";
}

export interface DocTemplateSlot {
  id: string;
  variable_id: string | null;
  /** internal_name of the bound variable, flattened by the API. */
  variable: string | null;
  variable_label: string | null;
  variable_type: DocumentDataType | null;
  paragraph_index: number;
  paragraph_kind: "body" | "table" | "header" | "footer";
  start_offset: number;
  end_offset: number;
  action: "replace_span" | "insert_after_label" | "insert_empty_cell";
  /** What sits at this position today — the text a generation overwrites. */
  original_text: string;
  context_before: string;
  context_after: string;
  confidence: number | null;
  status: ReviewStatus;
  source: "ai" | "manual";
}

export interface DocTemplateSchema {
  version: DocTemplateVersion;
  variables: DocTemplateVariable[];
  slots: DocTemplateSlot[];
  /** Above this, the builder pre-selects the field for confirmation. */
  confirm_threshold: number;
}

export interface DocAnalyzeResult {
  status: AnalysisStatus;
  detected_type: string | null;
  detected_type_confidence: number | null;
  candidates_found: number;
  variables_created: number;
  variables_refreshed: number;
  slots_created: number;
  slots_refreshed: number;
  truncated: boolean;
  error: string | null;
}

export interface DocPreviewResult {
  docx_url: string | null;
  pdf_url: string | null;
  warnings: string[];
  used_values: Record<string, unknown>;
}

export type GeneratedDocumentStatus =
  | "pending"
  | "validation_failed"
  | "generating"
  | "generation_failed"
  | "generated"
  | "approved"
  | "rejected"
  | "sending"
  | "sent"
  | "send_failed";

export interface ValidationIssue {
  severity: "error" | "warning";
  code: string;
  message: string;
  variable: string | null;
}

export interface ValidationReport {
  ok: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

export interface GeneratedFile {
  format: "docx" | "pdf";
  url: string | null;
}

export interface GeneratedDocument {
  id: string;
  status: GeneratedDocumentStatus;
  template_id: string;
  template_name: string | null;
  version_id: string;
  generation_no: number;
  onboarding_run_id: string | null;
  validation_report: ValidationReport | Record<string, never>;
  /** { values: {name: value}, sources: {name: "offer.start_date"} } */
  context_snapshot: {
    values?: Record<string, unknown>;
    sources?: Record<string, string | null>;
  };
  candidate_snapshot: Record<string, Record<string, unknown>>;
  error_message: string | null;
  files: GeneratedFile[];
  generated_at: string | null;
  approved_at: string | null;
  rejected_at: string | null;
  rejection_reason: string | null;
  created_at: string | null;
}

/** Outcome codes from POST /generated-documents. Only `generated` produced a
 * document; the rest are states HR resolves rather than errors to throw. */
export type GenerateOutcome =
  | "generated"
  | "missing_template"
  | "no_confirmed_fields"
  | "validation_failed"
  | "template_drift"
  | "render_failed"
  | "no_candidate";

export interface GenerateResult {
  outcome: GenerateOutcome;
  document: GeneratedDocument | null;
  warnings: string[];
  error: string | null;
}
