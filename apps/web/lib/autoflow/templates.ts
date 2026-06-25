import type { AutoflowDraft } from "./types";

export interface AutoflowTemplate {
  id: string;
  title: string;
  tagline: string;
  category: "documents" | "sales" | "ops" | "knowledge" | "compliance" | "scheduled";
  icon: string;
  draft: Omit<AutoflowDraft, "is_active">;
}

const tmpl = (id: string, title: string, tagline: string, category: AutoflowTemplate["category"], icon: string, draft: Omit<AutoflowDraft, "is_active">): AutoflowTemplate => ({
  id,
  title,
  tagline,
  category,
  icon,
  draft,
});

export const AUTOFLOW_TEMPLATES: AutoflowTemplate[] = [
  tmpl(
    "doc-ready-slack",
    "Announce new docs in Slack",
    "When a document finishes ingesting, summarise it and post to a channel.",
    "documents",
    "MessageSquare",
    {
      name: "Announce new docs in Slack",
      description: "Posts an auto-summary of every newly-ingested document to a Slack channel.",
      trigger_type: "document_ready",
      trigger_config: { filters: {} },
      actions: [
        {
          id: "s0",
          type: "generate_output",
          order: 0,
          config: {
            prompt: "Write a 3-sentence summary of \"{{trigger.document_name}}\" for a busy team channel. Highlight any pricing, deadlines, or owner names.",
          },
        },
        {
          id: "s1",
          type: "post_slack",
          order: 1,
          config: {
            channel_id: "",
            text: ":page_facing_up: *{{trigger.document_name}}* is now in the knowledge base.\n\n{{step_0.output.text}}",
          },
        },
      ],
      confidence_threshold: null,
    },
  ),
  tmpl(
    "doc-failed-notify",
    "Alert admins on ingestion failures",
    "When ingestion fails, notify admins and log the reason.",
    "ops",
    "FileX2",
    {
      name: "Alert admins on ingestion failures",
      description: "Catches failed ingestions before they go unnoticed.",
      trigger_type: "document_failed",
      trigger_config: { filters: {} },
      actions: [
        {
          id: "s0",
          type: "notify_admin",
          order: 0,
          config: {
            title: "Ingestion failed: {{trigger.document_name}}",
            body: "Reason: {{trigger.error}}",
            link_url: "/documents",
          },
        },
      ],
      confidence_threshold: null,
    },
  ),
  tmpl(
    "knowledge-gap-notion",
    "Capture knowledge gaps in Notion",
    "When the curator flags a gap, create a Notion page describing it for the content team.",
    "knowledge",
    "FileQuestion",
    {
      name: "Capture knowledge gaps in Notion",
      description: "Routes detected gaps to the content team's backlog.",
      trigger_type: "knowledge_gap_detected",
      trigger_config: { filters: {} },
      actions: [
        {
          id: "s0",
          type: "generate_output",
          order: 0,
          config: {
            prompt: "Write a short brief for a content writer to fill a knowledge gap about: {{trigger.topic}}.\nInclude: 1) what to write, 2) which existing docs to reference, 3) suggested audience.",
          },
        },
        {
          id: "s1",
          type: "create_notion_page",
          order: 1,
          config: {
            parent_page_id: "",
            title: "Knowledge gap: {{trigger.topic}}",
            content: "{{step_0.output.text}}",
          },
        },
      ],
      confidence_threshold: null,
    },
  ),
  tmpl(
    "weekly-digest",
    "Weekly knowledge digest email",
    "Every Monday morning, draft and email a digest of recent KB activity.",
    "scheduled",
    "Mail",
    {
      name: "Weekly knowledge digest",
      description: "Pushes a Monday morning summary to the team.",
      trigger_type: "scheduled",
      trigger_config: { cron: "0 9 * * 1" },
      actions: [
        {
          id: "s0",
          type: "generate_output",
          order: 0,
          config: {
            prompt: "Draft a Monday-morning digest of knowledge base highlights for our team.\nCover: most-asked topics, newly-added docs, and any knowledge gaps spotted last week.\nKeep it under 250 words, friendly tone.",
          },
        },
        {
          id: "s1",
          type: "send_email",
          order: 1,
          config: {
            to: "team@yourcompany.com",
            subject: "Weekly knowledge digest",
            body: "{{step_0.output.text}}",
          },
        },
      ],
      confidence_threshold: null,
    },
  ),
  tmpl(
    "negative-feedback-review",
    "Route negative feedback to admins",
    "When a user thumbs-downs an answer, surface it for review.",
    "knowledge",
    "ThumbsDown",
    {
      name: "Route negative feedback to admins",
      description: "Catches every thumbs-down so the team can fix gaps or weak docs.",
      trigger_type: "message_feedback_negative",
      trigger_config: { filters: {} },
      actions: [
        {
          id: "s0",
          type: "notify_admin",
          order: 0,
          config: {
            title: "Negative feedback on a response",
            body: "Q: {{trigger.query_text}}\n\nA: {{trigger.response_text}}",
            link_url: "/admin/feedback",
          },
        },
      ],
      confidence_threshold: null,
    },
  ),
  tmpl(
    "new-employee-welcome",
    "Welcome new employees with a brief",
    "When a teammate accepts an invite, draft a tailored onboarding brief.",
    "ops",
    "UserPlus",
    {
      name: "Welcome new employees",
      description: "Drafts an onboarding brief and emails it to every new joiner.",
      trigger_type: "employee_joined",
      trigger_config: {},
      actions: [
        {
          id: "s0",
          type: "generate_output",
          order: 0,
          config: {
            prompt: "Draft a warm welcome message for {{trigger.display_name}} who just joined as {{trigger.role}}.\nInclude: 3 must-read documents from our KB, 2 quick wins for week 1, and a friendly tone.",
          },
        },
        {
          id: "s1",
          type: "send_email",
          order: 1,
          config: {
            to: "{{trigger.email}}",
            subject: "Welcome to the team, {{trigger.display_name}}",
            body: "{{step_0.output.text}}",
          },
        },
      ],
      confidence_threshold: null,
    },
  ),
  tmpl(
    "approval-request-slack",
    "Notify Slack on approval requests",
    "Pings #approvals when something needs sign-off.",
    "ops",
    "CheckCircle2",
    {
      name: "Notify Slack on approval requests",
      description: "Ensures pending approvals don't sit forgotten.",
      trigger_type: "approval_requested",
      trigger_config: { filters: {} },
      actions: [
        {
          id: "s0",
          type: "post_slack",
          order: 0,
          config: {
            channel_id: "",
            text: ":warning: New approval needed — *{{trigger.item_type}}*\nReason: {{trigger.change_reason}}\n<{{trigger.link_url}}|Open in app>",
          },
        },
      ],
      confidence_threshold: null,
    },
  ),
  tmpl(
    "compliance-ack-log",
    "Log compliance acknowledgements",
    "Emit a webhook event on every policy acknowledgement for downstream audit logging.",
    "compliance",
    "ShieldCheck",
    {
      name: "Log compliance acknowledgements",
      description: "Streams every acknowledgement to an audit system via webhook.",
      trigger_type: "compliance_acknowledged",
      trigger_config: { filters: {} },
      actions: [
        {
          id: "s0",
          type: "emit_webhook",
          order: 0,
          config: {
            event: "compliance.acknowledged",
            payload: {},
          },
        },
      ],
      confidence_threshold: null,
    },
  ),
  tmpl(
    "high-stakes-draft",
    "Draft + human approval for high-stakes outputs",
    "Generates an output, then holds for admin review before posting publicly.",
    "ops",
    "ShieldCheck",
    {
      name: "Draft with human approval",
      description: "Generate, gate, then send. Use when AI output is customer-visible.",
      trigger_type: "knowledge_gap_detected",
      trigger_config: { filters: {} },
      actions: [
        {
          id: "s0",
          type: "generate_output",
          order: 0,
          config: {
            prompt: "Draft a public-facing FAQ entry for: {{trigger.topic}}. Keep it polished and source-backed.",
          },
        },
        {
          id: "s1",
          type: "hold_for_approval",
          order: 1,
          config: {
            preview_text: "{{step_0.output.text}}",
            note: "Auto-drafted FAQ entry — please review before publishing.",
          },
        },
        {
          id: "s2",
          type: "create_notion_page",
          order: 2,
          config: {
            parent_page_id: "",
            title: "FAQ: {{trigger.topic}}",
            content: "{{step_0.output.text}}",
          },
        },
      ],
      confidence_threshold: 0.6,
    },
  ),
  tmpl(
    "agent-completion-notify",
    "Notify admins when an agent finishes",
    "Heads up after every long-running agent run completes.",
    "ops",
    "Sparkles",
    {
      name: "Agent completion notifications",
      description: "Pings admins after every agent run.",
      trigger_type: "agent_completed",
      trigger_config: { filters: {} },
      actions: [
        {
          id: "s0",
          type: "notify_admin",
          order: 0,
          config: {
            title: "Agent run complete: {{trigger.agent_type}}",
            body: "Status: {{trigger.status}}",
            link_url: "/admin/agent-runs",
          },
        },
      ],
      confidence_threshold: null,
    },
  ),
  tmpl(
    "no-results-capture",
    "Capture failed searches as gaps",
    "When a search returns nothing, log it as a potential knowledge gap.",
    "knowledge",
    "SearchX",
    {
      name: "Capture failed searches",
      description: "Routes empty-result queries to the admin gap inbox.",
      trigger_type: "query_no_results",
      trigger_config: { filters: {} },
      actions: [
        {
          id: "s0",
          type: "notify_admin",
          order: 0,
          config: {
            title: "Search came back empty",
            body: "Query: {{trigger.query_text}}",
            link_url: "/admin/knowledge-gaps",
            dedupe_key: "no_results:{{trigger.query_text}}",
          },
        },
      ],
      confidence_threshold: null,
    },
  ),
  tmpl(
    "sales-pricing-watch",
    "Auto-brief sales on pricing doc changes",
    "When a pricing doc lands, draft a sales-ready summary and post to #sales.",
    "sales",
    "MessageSquare",
    {
      name: "Sales pricing watch",
      description: "Keeps the sales team in the loop on every pricing update.",
      trigger_type: "document_ready",
      trigger_config: { filters: { tags: ["pricing"] } },
      actions: [
        {
          id: "s0",
          type: "generate_output",
          order: 0,
          config: {
            prompt: "Write a one-screen sales-team briefing on the pricing changes in \"{{trigger.document_name}}\". Lead with what changed, then objection-handling tips. Be specific.",
            scope_tags: ["pricing"],
          },
        },
        {
          id: "s1",
          type: "post_slack",
          order: 1,
          config: {
            channel_id: "",
            text: ":moneybag: *Pricing update*: {{trigger.document_name}}\n\n{{step_0.output.text}}",
          },
        },
      ],
      confidence_threshold: null,
    },
  ),
  tmpl(
    "monthly-coverage-report",
    "Monthly KB coverage report",
    "First of each month: generate a coverage report and email admins.",
    "scheduled",
    "Clock",
    {
      name: "Monthly KB coverage report",
      description: "Auto-generates the start-of-month KB health snapshot.",
      trigger_type: "scheduled",
      trigger_config: { cron: "0 9 1 * *" },
      actions: [
        {
          id: "s0",
          type: "generate_output",
          order: 0,
          config: {
            prompt: "Write a monthly knowledge base coverage report.\nInclude: top-asked topics, docs added/updated, knowledge gaps still open, and 3 recommended next steps.",
          },
        },
        {
          id: "s1",
          type: "notify_admin",
          order: 1,
          config: {
            title: "Monthly KB coverage report",
            body: "{{step_0.output.text}}",
            link_url: "/admin/coverage",
          },
        },
      ],
      confidence_threshold: null,
    },
  ),
];

export const TEMPLATE_CATEGORIES: Array<{
  id: AutoflowTemplate["category"];
  label: string;
}> = [
  { id: "documents", label: "Documents" },
  { id: "knowledge", label: "Knowledge" },
  { id: "sales", label: "Sales" },
  { id: "ops", label: "Ops" },
  { id: "compliance", label: "Compliance" },
  { id: "scheduled", label: "Scheduled" },
];
