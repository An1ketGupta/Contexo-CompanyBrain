export type Json = string | number | boolean | null | { [key: string]: Json } | Json[];

export type OrgPlan = "free" | "starter" | "growth" | "business";
export type UserRole = "admin" | "member";
export type DocumentStatus = "pending" | "processing" | "ready" | "failed";
export type DocumentFileType = "pdf" | "docx" | "txt" | "md";
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

export interface MessageSource {
  chunk_id: string;
  doc_name: string;
  page_number: number | null;
  excerpt: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  org_id: string;
  role: MessageRole;
  content: string;
  sources: MessageSource[] | null;
  feedback: MessageFeedback | null;
  created_at: string;
}

// SSE event types emitted by POST /chat/stream
export type ChatStreamEvent =
  | { type: "searching"; query: string }
  | { type: "sources"; sources: MessageSource[] }
  | { type: "token"; token: string }
  | { type: "done" }
  | { type: "error"; message: string };
