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
        <div className="mb-4 inline-flex h-12 w-12 items-center justify-center rounded-xl bg-primary/10">
          <Book className="h-5 w-5 text-primary" />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">Help Center</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Answers to common questions about Company Brain.
        </p>
      </header>

      <div className="relative mb-8">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search help articles…"
          className="w-full rounded-xl border border-border bg-background py-2.5 pl-9 pr-4 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
          aria-label="Search help articles"
          autoFocus
        />
      </div>

      {totalShown === 0 ? (
        <EmptyResults query={query} />
      ) : (
        <div className="space-y-6">
          {CATEGORIES.map((cat) => {
            const items = grouped[cat];
            if (!items?.length) return null;
            return (
              <section key={cat}>
                <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {cat}
                </h2>
                <ul className="overflow-hidden rounded-xl border border-border divide-y divide-border bg-background">
                  {items.map((article) => (
                    <li key={article.slug}>
                      <Link
                        href={`/help/${article.slug}`}
                        className={cn(
                          "flex items-center gap-3 px-4 py-3 text-sm transition-colors",
                          "hover:bg-muted/50",
                        )}
                      >
                        <span className="flex-1 truncate text-foreground">
                          {article.title}
                        </span>
                        <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}

      <div className="mt-10 rounded-xl bg-muted/60 px-4 py-4 text-center text-sm text-muted-foreground">
        Can't find what you're looking for?{" "}
        <a
          href="mailto:support@companybrain.app"
          className="font-medium text-primary hover:underline"
        >
          Contact support →
        </a>
      </div>
    </div>
  );
}

function EmptyResults({ query }: { query: string }) {
  return (
    <div className="rounded-xl border border-dashed border-border px-6 py-12 text-center">
      <p className="text-sm text-muted-foreground">
        No articles match <span className="font-medium text-foreground">"{query}"</span>.
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        Try a broader keyword or{" "}
        <a
          href="mailto:support@companybrain.app"
          className="text-primary hover:underline"
        >
          contact support
        </a>
        .
      </p>
    </div>
  );
}
