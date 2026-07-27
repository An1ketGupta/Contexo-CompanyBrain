"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Book, ChevronRight, Search } from "lucide-react";

import { CATEGORIES, HELP_ARTICLES } from "./articles";
import { cn } from "@/lib/utils";

export default function HelpCenterPage() {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return HELP_ARTICLES;
    return HELP_ARTICLES.filter((a) =>
      a.title.toLowerCase().includes(q) ||
      a.tags.some((t) => t.toLowerCase().includes(q)) ||
      a.category.toLowerCase().includes(q),
    );
  }, [query]);

  const grouped = useMemo(() => {
    const map: Record<string, typeof HELP_ARTICLES> = {};
    for (const cat of CATEGORIES) {
      map[cat] = filtered.filter((a) => a.category === cat);
    }
    return map;
  }, [filtered]);

  const totalShown = filtered.length;

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 md:py-12">
      <header className="mb-8 text-center">
        <div className="mb-4 inline-flex size-12 items-center justify-center rounded-2xl bg-brand-tint">
          <Book className="size-5 text-brand" />
        </div>
        <p className="text-[13px] font-bold text-brand">HELP CENTER</p>
        <h1 className="mt-1 text-3xl font-extrabold tracking-tight">How can we help?</h1>
        <p className="mx-auto mt-1 max-w-[64ch] text-[15px] leading-relaxed text-muted-foreground">
          Answers to common questions about Contexo.
        </p>
      </header>

      <div className="relative mb-8">
        <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search help articles…"
          className="w-full rounded-full border border-border bg-card py-3 pl-10 pr-4 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
          aria-label="Search help articles"
          autoFocus
        />
      </div>

      {totalShown === 0 ? (
        <EmptyResults query={query} />
      ) : (
        <div className="space-y-7">
          {CATEGORIES.map((cat) => {
            const items = grouped[cat];
            if (!items?.length) return null;
            return (
              <section key={cat}>
                <h2 className="mb-2.5 font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
                  {cat}
                </h2>
                <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
                  {items.map((article) => (
                    <li key={article.slug}>
                      <Link
                        href={`/help/${article.slug}`}
                        className={cn(
                          "flex items-center gap-3 px-5 py-3.5 text-sm transition-colors",
                          "hover:bg-muted/50",
                        )}
                      >
                        <span className="flex-1 truncate font-medium text-foreground">
                          {article.title}
                        </span>
                        <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}

      <div className="mt-10 rounded-2xl border border-border bg-muted/60 px-5 py-5 text-center text-sm text-body">
        Can't find what you're looking for?{" "}
        <a
          href="mailto:support@companybrain.app"
          className="font-semibold text-brand hover:underline"
        >
          Contact support →
        </a>
      </div>
    </div>
  );
}

function EmptyResults({ query }: { query: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-muted/40 px-6 py-12 text-center">
      <p className="text-sm font-semibold text-foreground">
        No articles match <span className="text-brand">"{query}"</span>.
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        Try a broader keyword or{" "}
        <a
          href="mailto:support@companybrain.app"
          className="text-brand hover:underline"
        >
          contact support
        </a>
        .
      </p>
    </div>
  );
}
