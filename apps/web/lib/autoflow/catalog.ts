import type { ActionType, FieldSchema } from "./types";

export interface ActionField {
  key: string;
  label: string;
  type: "text" | "textarea" | "select" | "slack-channel" | "notion-page" | "tags" | "users" | "json" | "webhook-event";
  placeholder?: string;
  description?: string;
  required?: boolean;
  supportsVariables?: boolean;
  rows?: number;
  options?: Array<{ value: string; label: string }>;
}

export interface ActionCatalogEntry {
  type: ActionType;
  label: string;
  shortLabel: string;
  description: string;
  icon: string;
  category: "ai" | "notify" | "integrations" | "control";
  /** Whether this action is fully implemented in the backend. */
  available: boolean;
  fields: ActionField[];
  /** Fields the action's output exposes to subsequent steps (used by the variable picker). */
  outputFields: FieldSchema[];
}

export const ACTION_CATALOG: ActionCatalogEntry[] = [
  {
    type: "generate_output",
    label: "Generate output with KB",
    shortLabel: "AI draft",
    description: "Draft text using your knowledge base. Cites sources. Use this to write emails, summaries, briefs.",
    icon: "Sparkles",
    category: "ai",
    available: true,
    fields: [
      {
        key: "prompt",
        label: "Prompt",
        type: "textarea",
        rows: 6,
        required: true,
        supportsVariables: true,
        placeholder: "Draft a summary of {{trigger.document_name}} for our sales team. Focus on pricing changes.",
        description: "What you want the AI to write. Reference trigger data with {{trigger.fieldname}}.",
      },
      {
        key: "scope_tags",
        label: "Limit KB search to tags",
        type: "tags",
        description: "Restrict retrieval to documents with these tags. Leave empty to search everything.",
      },
      {
        key: "intent",
        label: "Intent hint",
        type: "select",
        description: "Override classification — useful when the prompt is ambiguous.",
        options: [
          { value: "", label: "Auto-detect" },
          { value: "writing", label: "Writing (drafts, emails)" },
          { value: "analysis", label: "Analysis (research, comparison)" },
        ],
      },
    ],
    outputFields: [
      { path: "text", label: "Generated text", example: "Here is the summary…" },
      { path: "sources", label: "Citations array" },
      { path: "confidence", label: "Model confidence (0-1)" },
    ],
  },
  {
    type: "send_email",
    label: "Send email",
    shortLabel: "Email",
    description: "Send a transactional email via the org's outbound sender.",
    icon: "Mail",
    category: "notify",
    available: true,
    fields: [
      {
        key: "to",
        label: "To",
        type: "text",
        required: true,
        supportsVariables: true,
        placeholder: "you@example.com or {{trigger.email}}",
      },
      {
        key: "subject",
        label: "Subject",
        type: "text",
        required: true,
        supportsVariables: true,
        placeholder: "New: {{trigger.document_name}}",
      },
      {
        key: "body",
        label: "Body",
        type: "textarea",
        rows: 8,
        required: true,
        supportsVariables: true,
        placeholder: "Hi, the team just uploaded {{trigger.document_name}}.\n\n{{step_0.output.text}}",
      },
      {
        key: "dedupe_key",
        label: "Dedupe key (optional)",
        type: "text",
        supportsVariables: true,
        description: "Prevent duplicate sends from retries. Defaults to a per-run key.",
      },
    ],
    outputFields: [
      { path: "queued", label: "Whether the email was queued" },
      { path: "to", label: "Recipient" },
      { path: "subject", label: "Subject sent" },
    ],
  },
  {
    type: "post_slack",
    label: "Post to Slack",
    shortLabel: "Slack",
    description: "Post a message to a Slack channel via the connected bot.",
    icon: "MessageSquare",
    category: "integrations",
    available: true,
    fields: [
      {
        key: "channel_id",
        label: "Channel",
        type: "slack-channel",
        required: true,
        description: "The bot must be invited to private channels.",
      },
      {
        key: "text",
        label: "Message",
        type: "textarea",
        rows: 6,
        required: true,
        supportsVariables: true,
        placeholder: "📄 {{trigger.document_name}} is ready.\n{{step_0.output.text}}",
        description: "Supports Slack mrkdwn.",
      },
      {
        key: "thread_ts",
        label: "Thread timestamp (optional)",
        type: "text",
        supportsVariables: true,
        description: "Reply in a thread instead of posting top-level.",
      },
    ],
    outputFields: [
      { path: "ts", label: "Posted message timestamp" },
      { path: "channel", label: "Channel ID" },
    ],
  },
  {
    type: "create_notion_page",
    label: "Create Notion page",
    shortLabel: "Notion",
    description: "Create a new Notion page under the chosen parent.",
    icon: "FileText",
    category: "integrations",
    available: true,
    fields: [
      {
        key: "parent_page_id",
        label: "Parent page",
        type: "notion-page",
        required: true,
        description: "Bot must have access to this page.",
      },
      {
        key: "title",
        label: "Title",
        type: "text",
        required: true,
        supportsVariables: true,
        placeholder: "Brief: {{trigger.document_name}}",
      },
      {
        key: "content",
        label: "Content",
        type: "textarea",
        rows: 8,
        required: true,
        supportsVariables: true,
        placeholder: "{{step_0.output.text}}",
      },
    ],
    outputFields: [
      { path: "page_id", label: "Created page ID" },
      { path: "url", label: "Page URL" },
    ],
  },
  {
    type: "notify_admin",
    label: "Notify admins",
    shortLabel: "Notify",
    description: "Send an in-app notification to every admin in the org.",
    icon: "Bell",
    category: "notify",
    available: true,
    fields: [
      {
        key: "title",
        label: "Title",
        type: "text",
        required: true,
        supportsVariables: true,
        placeholder: "Knowledge gap detected: {{trigger.topic}}",
      },
      {
        key: "body",
        label: "Body",
        type: "textarea",
        rows: 4,
        supportsVariables: true,
        placeholder: "Optional details…",
      },
      {
        key: "link_url",
        label: "Link URL",
        type: "text",
        supportsVariables: true,
        placeholder: "/admin/knowledge-gaps",
      },
      {
        key: "dedupe_key",
        label: "Dedupe key (optional)",
        type: "text",
        supportsVariables: true,
      },
    ],
    outputFields: [{ path: "delivered_count", label: "Admins notified" }],
  },
  {
    type: "emit_webhook",
    label: "Emit webhook",
    shortLabel: "Webhook",
    description: "Trigger an outbound webhook event. Subscribers configured in Settings → Webhooks.",
    icon: "Webhook",
    category: "integrations",
    available: true,
    fields: [
      {
        key: "event",
        label: "Event name",
        type: "webhook-event",
        required: true,
        placeholder: "autoflow.fired",
      },
      {
        key: "payload",
        label: "Payload JSON",
        type: "json",
        description: "Additional fields to merge into the webhook payload.",
      },
    ],
    outputFields: [
      { path: "queued_count", label: "Subscribers notified" },
      { path: "event", label: "Event name" },
    ],
  },
  {
    type: "hold_for_approval",
    label: "Hold for approval",
    shortLabel: "Approval gate",
    description: "Pause the flow until a human admin approves. Subsequent actions run after approval.",
    icon: "ShieldCheck",
    category: "control",
    available: true,
    fields: [
      {
        key: "approver_user_id",
        label: "Approver (optional)",
        type: "users",
        description: "Defaults to any admin in the org.",
      },
      {
        key: "preview_text",
        label: "Preview shown to approver",
        type: "textarea",
        rows: 4,
        supportsVariables: true,
        placeholder: "{{step_0.output.text}}",
      },
      {
        key: "note",
        label: "Note",
        type: "text",
        supportsVariables: true,
      },
    ],
    outputFields: [{ path: "approval_id", label: "Approval row ID" }],
  },
  {
    type: "create_task",
    label: "Create task (Asana / Linear)",
    shortLabel: "Task",
    description: "Reserved — task adapters land in a future sprint.",
    icon: "ListChecks",
    category: "integrations",
    available: false,
    fields: [],
    outputFields: [],
  },
];

export function getAction(type: ActionType): ActionCatalogEntry {
  const found = ACTION_CATALOG.find((a) => a.type === type);
  if (!found) throw new Error(`Unknown action type: ${type}`);
  return found;
}

export const ACTION_CATEGORIES: Array<{
  id: ActionCatalogEntry["category"];
  label: string;
}> = [
  { id: "ai", label: "AI" },
  { id: "notify", label: "Notifications" },
  { id: "integrations", label: "Integrations" },
  { id: "control", label: "Control flow" },
];

export const WEBHOOK_EVENT_OPTIONS = [
  "autoflow.fired",
  "document.ingested",
  "knowledge_gap.detected",
  "approval.requested",
  "compliance.acknowledged",
  "agent.completed",
  "custom.event",
];
