import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronLeft } from "lucide-react";

import { HELP_ARTICLES } from "../articles";
import { loadArticle } from "../lib";
import { Markdown } from "@/components/chat/markdown";
import { ArticleFeedback } from "./feedback";

// Pre-render every known article at build time. dynamicParams=false rejects
// any slug not in the manifest — paired with the slug regex in loadArticle,
// this is the second layer of defence against arbitrary path reads.
export const dynamicParams = false;

export async function generateStaticParams() {
  return HELP_ARTICLES.map((a) => ({ slug: a.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const article = await loadArticle(slug);
  if (!article) return { title: "Help — Nirnaya IQ" };
  return {
    title: `${article.frontmatter.title} — Help — Nirnaya IQ`,
    description: undefined,
  };
}

export default async function HelpArticlePage({
  params,
}: {
  // Next.js 16: params is a promise and must be awaited.
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const article = await loadArticle(slug);
  if (!article) notFound();

  return (
    <div className="mx-auto max-w-2xl px-4 py-8 md:py-10">
      <Link
        href="/help"
        className="mb-6 inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronLeft className="h-4 w-4" />
        Help Center
      </Link>

      <header className="mb-6">
        <span className="text-xs font-semibold uppercase tracking-wider text-primary">
          {article.frontmatter.category}
        </span>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          {article.frontmatter.title}
        </h1>
      </header>

      <article className="text-foreground">
        <Markdown>{article.body}</Markdown>
      </article>

      <ArticleFeedback slug={slug} />
    </div>
  );
}
