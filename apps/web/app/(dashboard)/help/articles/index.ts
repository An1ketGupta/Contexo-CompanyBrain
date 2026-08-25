// V4 Day 4: help-center article manifest.
//
// Mirrors the .md files in this directory. Kept as a hand-maintained list
// rather than a build-time glob because:
//   1. The index is consumed by client components (search box, sidebar, command
//      palette), so we need it as plain JS, not a server-only fs call.
//   2. Order, category grouping, and visibility-control beyond "all .md files"
//      is easier to express in TS than in fs scaffolding.
// Adding an article = drop a .md file in this directory and add a line below.

export type HelpCategory =
  | "Team & Access"
  | "Documents"
  | "AI & Search"
  | "Features"
  | "Integrations"
  | "Account";

export interface HelpArticleMeta {
  slug: string;
  title: string;
  category: HelpCategory;
  tags: readonly string[];
}

// Order of CATEGORIES drives the help index — Team first because invites are
// the typical first-touch task for a new admin.
export const CATEGORIES: readonly HelpCategory[] = [
  "Team & Access",
  "Documents",
  "AI & Search",
  "Features",
  "Integrations",
  "Account",
] as const;

export const HELP_ARTICLES: readonly HelpArticleMeta[] = [
  {
    slug: "invite-teammates",
    title: "How do I invite a teammate?",
    category: "Team & Access",
    tags: ["invite", "team", "users", "access", "members", "admin"],
  },
  {
    slug: "upload-documents",
    title: "How do I upload documents?",
    category: "Documents",
    tags: ["upload", "documents", "drag", "drop", "files", "ingest"],
  },
  {
    slug: "supported-file-types",
    title: "What file types are supported?",
    category: "Documents",
    tags: ["pdf", "docx", "xlsx", "pptx", "txt", "md", "html", "csv", "files", "formats"],
  },
  {
    slug: "troubleshooting",
    title: "Troubleshooting document processing",
    category: "Documents",
    tags: ["failed", "retry", "processing", "error", "stuck", "slow", "ingest"],
  },
  {
    slug: "how-search-works",
    title: "How does the AI search my documents?",
    category: "AI & Search",
    tags: ["search", "ai", "embeddings", "rag", "vector", "hybrid", "retrieval", "citations"],
  },
  {
    slug: "prompt-templates",
    title: "Using prompt templates",
    category: "Features",
    tags: ["templates", "prompts", "shortcuts", "snippets", "library"],
  },
  {
    slug: "slack-integration",
    title: "Setting up the Slack integration",
    category: "Integrations",
    tags: ["slack", "bot", "slash command", "integration", "dm", "channels"],
  },
  {
    slug: "billing-and-plans",
    title: "Billing and plan limits",
    category: "Account",
    tags: ["billing", "plans", "quota", "upgrade", "pricing", "payment", "invoice"],
  },
  {
    slug: "dark-mode",
    title: "How to enable dark mode",
    category: "Account",
    tags: ["dark mode", "light mode", "theme", "settings", "appearance", "system"],
  },
] as const;
