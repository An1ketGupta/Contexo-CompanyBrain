interface DocumentSummaryProps {
  summary: string;
  keyTopics?: string[];
}

/**
 * Auto-generated summary + extracted topics. Rendered as two distinct
 * subsections inside the expanded document panel. Topics deep-link into
 * /chat with the topic seeded as the query.
 */
export function DocumentSummary({ summary, keyTopics }: DocumentSummaryProps) {
  return (
    <div className="space-y-5">
      <section>
        <SectionLabel>Summary</SectionLabel>
        <p className="mt-1.5 text-sm leading-relaxed text-foreground/85">
          {summary}
        </p>
      </section>

      {keyTopics && keyTopics.length > 0 && (
        <section>
          <SectionLabel>Key topics</SectionLabel>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            {keyTopics.map((topic, i) => (
              <span key={topic} className="flex items-center gap-3">
                <span className="text-sm text-muted-foreground">{topic}</span>
                {i < keyTopics.length - 1 && (
                  <span aria-hidden className="text-muted-foreground/40">·</span>
                )}
              </span>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
      {children}
    </h4>
  );
}
