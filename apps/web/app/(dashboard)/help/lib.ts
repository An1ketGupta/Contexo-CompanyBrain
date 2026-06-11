// Server-only helpers for reading help-center articles off disk.
//
// We deliberately do NOT pull in `gray-matter`: our frontmatter is dead simple
// (`---` delimited, flat key: value pairs, no nested YAML or expressions), and
// adding a 30 KB dep for one regex-friendly parse buys nothing. If frontmatter
// ever needs anything fancier, swap this for `gray-matter` and the public
// signature here is identical.

// Node-only imports below ensure this module fails fast if a client component
// ever imports it — no `server-only` dep needed.
import { promises as fs } from "node:fs";
import path from "node:path";

export interface ArticleFrontmatter {
  title: string;
  category: string;
  order: number;
  tags: string[];
}

export interface ArticleFile {
  frontmatter: ArticleFrontmatter;
  body: string;
}

const ARTICLES_DIR = path.join(process.cwd(), "app", "(dashboard)", "help", "articles");

// Pre-anchored — must match top of file. We don't accept BOMs (Windows
// editors occasionally insert one); if you see "title undefined" in an
// article, re-save the .md as UTF-8 without BOM.
const FRONTMATTER_RE = /^---\r?\n([\s\S]*?)\r?\n---\r?\n([\s\S]*)$/;

export async function loadArticle(slug: string): Promise<ArticleFile | null> {
  // Defense against `../../etc/passwd`-style traversal. Slugs are scoped to
  // the static manifest at runtime, but generateStaticParams reads from the
  // filesystem and a typo or future codepath could in theory pass anything.
  if (!/^[a-z0-9-]+$/.test(slug)) return null;

  const filePath = path.join(ARTICLES_DIR, `${slug}.md`);
  let raw: string;
  try {
    raw = await fs.readFile(filePath, "utf-8");
  } catch (err: unknown) {
    if (typeof err === "object" && err !== null && "code" in err && (err as { code: string }).code === "ENOENT") {
      return null;
    }
    throw err;
  }

  const match = raw.match(FRONTMATTER_RE);
  if (!match) {
    // No frontmatter — render the file as body-only with placeholders. We
    // surface this as a soft error rather than throw so a half-finished
    // article still renders during local edits.
    return {
      frontmatter: { title: slug, category: "Other", order: 999, tags: [] },
      body: raw,
    };
  }

  return {
    frontmatter: parseFrontmatter(match[1]),
    body: match[2].trimStart(),
  };
}

function parseFrontmatter(yaml: string): ArticleFrontmatter {
  // Each line is `key: value` or `key: [a, b, c]`. We intentionally don't
  // support nested objects, multi-line strings, or quoting beyond stripping
  // matched outer quotes — keeps this 20 lines and predictable.
  const data: Record<string, string | number | string[]> = {};
  for (const line of yaml.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const colonIdx = trimmed.indexOf(":");
    if (colonIdx === -1) continue;
    const key = trimmed.slice(0, colonIdx).trim();
    let value: string = trimmed.slice(colonIdx + 1).trim();

    // Strip matched surrounding quotes once.
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }

    if (value.startsWith("[") && value.endsWith("]")) {
      data[key] = value
        .slice(1, -1)
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      continue;
    }

    if (key === "order") {
      const n = Number(value);
      data[key] = Number.isFinite(n) ? n : 999;
      continue;
    }

    data[key] = value;
  }

  return {
    title: typeof data.title === "string" ? data.title : "Untitled",
    category: typeof data.category === "string" ? data.category : "Other",
    order: typeof data.order === "number" ? data.order : 999,
    tags: Array.isArray(data.tags) ? data.tags : [],
  };
}
