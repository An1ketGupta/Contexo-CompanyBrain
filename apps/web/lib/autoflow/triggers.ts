import type { FieldSchema, TriggerType } from "./types";

export interface TriggerCatalogEntry {
  type: TriggerType;
  label: string;
  description: string;
  icon: string;
  group: "documents" | "knowledge" | "people" | "approvals" | "schedule" | "agents";
  acceptsFilters: boolean;
  acceptsCron: boolean;
  payloadFields: FieldSchema[];
}

export const TRIGGER_CATALOG: TriggerCatalogEntry[] = [
  {
    type: "document_uploaded",
    label: "Document uploaded",
    description: "Fires when a document is uploaded, before ingestion completes.",
    icon: "Upload",
    group: "documents",
    acceptsFilters: true,
    acceptsCron: false,
    payloadFields: [
      { path: "document_id", label: "Document ID", example: "uuid" },
      { path: "document_name", label: "Document name", example: "Pricing Q3.pdf" },
      { path: "file_type", label: "File type", example: "pdf" },
      { path: "uploader_id", label: "Uploader user ID" },
      { path: "tags", label: "Tags (array)" },
    ],
  },
  {
    type: "document_ready",
    label: "Document ready",
    description: "Fires after a document finishes ingesting and is searchable.",
    icon: "FileCheck2",
    group: "documents",
    acceptsFilters: true,
    acceptsCron: false,
    payloadFields: [
      { path: "document_id", label: "Document ID" },
      { path: "document_name", label: "Document name" },
      { path: "summary", label: "Auto-generated summary" },
      { path: "chunk_count", label: "Number of chunks" },
      { path: "tags", label: "Tags (array)" },
      { path: "collection_id", label: "Collection ID" },
    ],
  },
  {
    type: "document_failed",
    label: "Document failed to ingest",
    description: "Fires when a document fails parsing or embedding.",
    icon: "FileX2",
    group: "documents",
    acceptsFilters: true,
    acceptsCron: false,
    payloadFields: [
      { path: "document_id", label: "Document ID" },
      { path: "document_name", label: "Document name" },
      { path: "error", label: "Failure reason" },
    ],
  },
  {
    type: "query_no_results",
    label: "Query returned no results",
    description: "Fires when a user search finds nothing relevant in the KB.",
    icon: "SearchX",
    group: "knowledge",
    acceptsFilters: true,
    acceptsCron: false,
    payloadFields: [
      { path: "query_text", label: "What the user asked" },
      { path: "user_id", label: "User ID" },
      { path: "conversation_id", label: "Conversation ID" },
    ],
  },
  {
    type: "message_feedback_negative",
    label: "Negative feedback on a message",
    description: "Fires when a user thumbs-down an assistant message.",
    icon: "ThumbsDown",
    group: "knowledge",
    acceptsFilters: true,
    acceptsCron: false,
    payloadFields: [
      { path: "message_id", label: "Message ID" },
      { path: "conversation_id", label: "Conversation ID" },
      { path: "user_id", label: "User ID" },
      { path: "query_text", label: "User question" },
      { path: "response_text", label: "Assistant response" },
    ],
  },
  {
    type: "scheduled",
    label: "On a schedule (cron)",
    description: "Fires at the cadence you set. Use for digests, reports, audits.",
    icon: "Clock",
    group: "schedule",
    acceptsFilters: false,
    acceptsCron: true,
    payloadFields: [
      { path: "scheduled_at", label: "Fire time (ISO 8601)" },
    ],
  },
  {
    type: "employee_joined",
    label: "Employee joined",
    description: "Fires when a teammate accepts their invitation.",
    icon: "UserPlus",
    group: "people",
    acceptsFilters: false,
    acceptsCron: false,
    payloadFields: [
      { path: "user_id", label: "New user ID" },
      { path: "email", label: "Email" },
      { path: "display_name", label: "Name" },
      { path: "role", label: "Role" },
    ],
  },
  {
    type: "knowledge_gap_detected",
    label: "Knowledge gap detected",
    description: "Fires when the curator agent flags an under-served topic.",
    icon: "FileQuestion",
    group: "knowledge",
    acceptsFilters: true,
    acceptsCron: false,
    payloadFields: [
      { path: "topic", label: "Gap topic" },
      { path: "query_examples", label: "Sample queries (array)" },
      { path: "frequency", label: "How often this gap was hit" },
    ],
  },
  {
    type: "approval_requested",
    label: "Approval requested",
    description: "Fires when a workflow item is submitted for review.",
    icon: "CheckCircle2",
    group: "approvals",
    acceptsFilters: true,
    acceptsCron: false,
    payloadFields: [
      { path: "approval_id", label: "Approval ID" },
      { path: "item_type", label: "Item type" },
      { path: "creator_id", label: "Submitter user ID" },
      { path: "change_reason", label: "Reason supplied" },
    ],
  },
  {
    type: "agent_completed",
    label: "Agent run completed",
    description: "Fires when a background agent finishes.",
    icon: "Sparkles",
    group: "agents",
    acceptsFilters: true,
    acceptsCron: false,
    payloadFields: [
      { path: "agent_type", label: "Agent type" },
      { path: "run_id", label: "Run ID" },
      { path: "status", label: "Result status" },
      { path: "result_json", label: "Structured result" },
    ],
  },
  {
    type: "compliance_acknowledged",
    label: "Compliance acknowledged",
    description: "Fires when a user signs off on a policy.",
    icon: "ShieldCheck",
    group: "approvals",
    acceptsFilters: true,
    acceptsCron: false,
    payloadFields: [
      { path: "policy_id", label: "Policy ID" },
      { path: "policy_name", label: "Policy name" },
      { path: "user_id", label: "User who acknowledged" },
      { path: "acknowledged_at", label: "Timestamp" },
    ],
  },
];

export function getTrigger(type: TriggerType): TriggerCatalogEntry {
  const found = TRIGGER_CATALOG.find((t) => t.type === type);
  if (!found) throw new Error(`Unknown trigger type: ${type}`);
  return found;
}

export const TRIGGER_GROUPS: Array<{
  id: TriggerCatalogEntry["group"];
  label: string;
}> = [
  { id: "documents", label: "Documents" },
  { id: "knowledge", label: "Knowledge & search" },
  { id: "people", label: "People" },
  { id: "approvals", label: "Approvals & compliance" },
  { id: "agents", label: "Agents" },
  { id: "schedule", label: "Schedule" },
];
