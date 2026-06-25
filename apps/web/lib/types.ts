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
  | "csv";
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
