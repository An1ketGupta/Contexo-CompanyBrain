import type { Document } from "@/lib/types";
import type { TocEntry } from "./table-of-contents";

/** Pure readers over the `documents.metadata` JSONB blob. Centralised so the
 *  shape stays in one place — every other component imports from here, so a
 *  schema change is a single-file update. */

export interface DocumentInsights {
  summary: string | null;
  keyTopics: string[];
  summaryGeneratedAt: string | null;
  toc: TocEntry[];
}

export function readDocumentInsights(doc: Pick<Document, "metadata">): DocumentInsights {
  const meta = (doc.metadata && typeof doc.metadata === "object")
    ? (doc.metadata as Record<string, unknown>)
    : {};

  const summary = typeof meta.summary === "string" && meta.summary.trim()
    ? meta.summary
    : null;

  const keyTopics: string[] = Array.isArray(meta.key_topics)
    ? meta.key_topics.filter((t): t is string => typeof t === "string" && t.trim().length > 0)
    : [];

  const summaryGeneratedAt = typeof meta.summary_generated_at === "string"
    ? meta.summary_generated_at
    : null;

  const tocRaw = Array.isArray(meta.toc) ? meta.toc : [];
  const toc: TocEntry[] = [];
  for (const item of tocRaw) {
    if (!item || typeof item !== "object") continue;
    const obj = item as Record<string, unknown>;
    const level = Number(obj.level);
    const title = typeof obj.title === "string" ? obj.title : null;
    if (!Number.isInteger(level) || level < 1 || level > 6) continue;
    if (!title || !title.trim()) continue;
    const page = typeof obj.page === "number" ? obj.page : null;
    toc.push({ level, title, page });
  }

  return { summary, keyTopics, summaryGeneratedAt, toc };
}

export function hasInsights(insights: DocumentInsights): boolean {
  return !!insights.summary || insights.keyTopics.length > 0 || insights.toc.length > 0;
}
