"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

interface MarkdownProps {
  children: string;
  className?: string;
}

/**
 * Constrained markdown renderer for assistant output.
 *
 * Safety: react-markdown does not allow raw HTML by default and we don't pass
 * `rehype-raw`, so user-supplied document content surfaced through the LLM
 * cannot escape the markdown sandbox via `<script>` etc.
 *
 * Style: prose-y typography tuned for chat — tight headings, real code blocks,
 * lists that look like lists. No syntax highlighting (would pull in 100KB);
 * code blocks render as preformatted text.
 */
export function Markdown({ children, className }: MarkdownProps) {
  return (
    <div
      className={cn(
        "prose-chat max-w-none text-sm leading-6 text-foreground",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: (props) => (
            <h1 className="mb-3 mt-4 text-lg font-semibold tracking-tight first:mt-0" {...props} />
          ),
          h2: (props) => (
            <h2 className="mb-2 mt-4 text-base font-semibold tracking-tight first:mt-0" {...props} />
          ),
          h3: (props) => (
            <h3 className="mb-2 mt-3 text-sm font-semibold first:mt-0" {...props} />
          ),
          p: (props) => <p className="mb-3 last:mb-0" {...props} />,
          ul: (props) => (
            <ul className="my-3 list-disc space-y-1 pl-5 marker:text-muted-foreground" {...props} />
          ),
          ol: (props) => (
            <ol className="my-3 list-decimal space-y-1 pl-5 marker:text-muted-foreground" {...props} />
          ),
          li: (props) => <li className="leading-6" {...props} />,
          a: (props) => (
            <a
              className="text-primary underline underline-offset-2 hover:no-underline"
              target="_blank"
              rel="noopener noreferrer"
              {...props}
            />
          ),
          strong: (props) => <strong className="font-semibold text-foreground" {...props} />,
          em: (props) => <em className="italic" {...props} />,
          hr: (props) => <hr className="my-4 border-border" {...props} />,
          blockquote: (props) => (
            <blockquote
              className="my-3 border-l-2 border-border pl-3 text-muted-foreground"
              {...props}
            />
          ),
          code: ({ className: codeClassName, children: codeChildren, ...rest }) => {
            const isBlock = (codeClassName ?? "").startsWith("language-");
            if (isBlock) {
              return (
                <code
                  className="block whitespace-pre-wrap break-words rounded-md border border-border bg-muted px-3 py-2 font-mono text-xs leading-5"
                  {...rest}
                >
                  {codeChildren}
                </code>
              );
            }
            return (
              <code
                className="rounded bg-muted px-1 py-0.5 font-mono text-[0.8125rem] text-foreground"
                {...rest}
              >
                {codeChildren}
              </code>
            );
          },
          pre: ({ children }) => <pre className="my-3 overflow-x-auto">{children}</pre>,
          table: (props) => (
            <div className="my-3 overflow-x-auto">
              <table className="w-full border-collapse text-xs" {...props} />
            </div>
          ),
          th: (props) => (
            <th
              className="border-b border-border px-2 py-1.5 text-left font-medium text-muted-foreground"
              {...props}
            />
          ),
          td: (props) => <td className="border-b border-border px-2 py-1.5 align-top" {...props} />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
