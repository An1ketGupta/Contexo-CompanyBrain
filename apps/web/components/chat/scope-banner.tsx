"use client";

import Link from "next/link";
import { FileText, Tag, X } from "lucide-react";

interface DocScopeProps {
  documentName: string;
  tags?: never;
  /** Render the clear-button only on the new-chat page (where scope is editable). */
  clearHref?: string;
}

interface TagScopeProps {
  tags: string[];
  documentName?: never;
  clearHref?: string;
}

type ScopeBannerProps = DocScopeProps | TagScopeProps;

/**
 * Inline banner shown at the top of a scoped chat so the user knows the AI
 * is only consulting a subset of the knowledge base. On the new-chat
 * surface the "Search all" link clears the scope by replacing the URL; on
 * persisted conversations scope is fixed (we don't allow rescoping mid-
 * thread because earlier answers were grounded against the original scope).
 */
export function ScopeBanner(props: ScopeBannerProps) {
  const isTagScope = "tags" in props && Array.isArray(props.tags);
  const { clearHref } = props;

  return (
    <div className="flex items-center gap-2 border-b border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-900 dark:border-blue-900/60 dark:bg-blue-950/40 dark:text-blue-200">
      {isTagScope ? (
        <>
          <Tag className="h-4 w-4 shrink-0" />
          <span className="min-w-0 truncate">
            Asking documents tagged{" "}
            <strong className="font-medium">
              {props.tags!.join(", ")}
            </strong>
          </span>
        </>
      ) : (
        <>
          <FileText className="h-4 w-4 shrink-0" />
          <span className="min-w-0 truncate">
            Asking only <strong className="font-medium">{props.documentName}</strong>
          </span>
        </>
      )}
      {clearHref && (
        <Link
          href={clearHref}
          className="ml-auto inline-flex items-center gap-1 text-xs text-blue-700 underline-offset-2 hover:underline dark:text-blue-300"
        >
          Search all documents
          <X className="h-3 w-3" />
        </Link>
      )}
    </div>
  );
}
