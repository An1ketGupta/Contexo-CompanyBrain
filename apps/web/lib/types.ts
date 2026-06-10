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
  page_number: number | null;
  section_heading: string | null;
  excerpt: string;
  snippet: string | null;
}

export type ConfidenceLevel = "high" | "medium" | "low";

export interface MessageConfidence {
  level: ConfidenceLevel;
  // 0–10 scale, matching the backend.
  score: number;
  // Number of cited chunks the confidence was averaged over.
  n: number;
}

export interface MessageMetadata {
  confidence?: MessageConfidence;
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

// SSE event types emitted by POST /chat/stream. Shape mirrors what
// app/api/routers/chat.py:_event_to_payload sends on the wire.
export type ChatStreamEvent =
  | { type: "start"; conversation_id: string }
  | { type: "searching"; query: string }
  | { type: "searched"; query: string; hit_count: number }
  | { type: "sources"; sources: MessageSource[] }
  | { type: "token"; text: string }
  | { type: "knowledge_gap"; topics: string[] }
  | {
      type: "confidence";
      level: ConfidenceLevel;
      score: number;
      chunks_considered: number;
    }
  | { type: "done"; message_id: string; tool_calls: number }
  | { type: "error"; message: string };
