import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

/**
 * Weekly admin digest (Day 11).
 *
 * The hero stat is **time saved** — that's the metric admins actually care
 * about and it sets the framing for everything else (gaps, low-confidence
 * answers, ack compliance). Backwards-compatible with the old shape —
 * legacy `query_count`/`doc_count`/`active_users` props are still
 * rendered, just below the new hero.
 */
export interface WeeklyDigestEmailProps {
  org_name: string;
  app_url: string;

  // Hero stat — emphasised at the top.
  time_saved_minutes?: number;
  time_saved_hours?: number;

  // Core engagement.
  query_count: number;
  doc_count: number;
  active_users: number;
  new_document_count?: number;
  new_document_titles?: string[];

  // Quality signals.
  negative_feedback_count?: number;
  positive_feedback_count?: number;
  low_confidence_count?: number;

  // Knowledge health.
  knowledge_gaps_count?: number;
  top_gap_topics?: { topic: string; count: number }[];

  // Compliance roll-up (Day 10 surface).
  ack_pending_count?: number;
  ack_completion_pct?: number | null;

  top_intents?: { intent: string; count: number }[];

  // Legacy.
  top_doc?: string | null;
}

export function WeeklyDigestEmail(props: WeeklyDigestEmailProps) {
  const {
    org_name,
    app_url,
    time_saved_hours = 0,
    time_saved_minutes = 0,
    query_count,
    doc_count,
    active_users,
    new_document_count = 0,
    new_document_titles = [],
    negative_feedback_count = 0,
    positive_feedback_count = 0,
    low_confidence_count = 0,
    knowledge_gaps_count = 0,
    top_gap_topics = [],
    ack_pending_count = 0,
    ack_completion_pct,
    top_intents = [],
  } = props;

  const baseUrl = app_url.replace(/\/$/, "");
  const heroValue = formatTimeSaved(time_saved_hours, time_saved_minutes);

  // Three concrete recommendations based on what the data says.
  const actions = recommendActions({
    knowledge_gaps_count,
    low_confidence_count,
    ack_pending_count,
    negative_feedback_count,
    new_document_count,
  });

  return (
    <EmailShell
      preview={`${org_name} — ${heroValue} saved this week`}
      heading={`${org_name} — weekly report`}
    >
      <Text style={p}>Here&apos;s how Contexo worked for your team this week.</Text>

      {/* Hero — time saved */}
      <Section style={hero}>
        <Text style={heroLabel}>Time saved this week</Text>
        <Text style={heroNumber}>{heroValue}</Text>
        <Text style={heroSub}>
          Across {query_count} {query_count === 1 ? "query" : "queries"} run by {active_users}{" "}
          {active_users === 1 ? "person" : "people"}.
        </Text>
      </Section>

      {/* Stats grid */}
      <Section style={{ margin: "24px 0" }}>
        <Stat label="Queries answered" value={query_count} />
        <Stat label="Active people" value={active_users} />
        <Stat label="Documents in knowledge base" value={doc_count} />
        {new_document_count > 0 && (
          <Stat label="New documents added" value={new_document_count} />
        )}
        {knowledge_gaps_count > 0 && (
          <Stat label="Knowledge gaps surfaced" value={knowledge_gaps_count} />
        )}
        {low_confidence_count > 0 && (
          <Stat label="Low-confidence answers" value={low_confidence_count} />
        )}
        {(positive_feedback_count > 0 || negative_feedback_count > 0) && (
          <Stat
            label="Feedback received"
            value={`${positive_feedback_count} 👍 · ${negative_feedback_count} 👎`}
          />
        )}
        {ack_pending_count > 0 && (
          <Stat
            label="Pending policy acknowledgements"
            value={
              ack_completion_pct != null
                ? `${ack_pending_count} · ${ack_completion_pct}% complete`
                : ack_pending_count
            }
          />
        )}
      </Section>

      {/* Knowledge gaps callout */}
      {top_gap_topics.length > 0 && (
        <Section style={callout}>
          <Text style={calloutHeading}>Topics your team asked but Contexo couldn&apos;t answer</Text>
          {top_gap_topics.map((t) => (
            <Text key={t.topic} style={listItem}>
              • <strong>{t.topic}</strong> — asked {t.count}×
            </Text>
          ))}
          <Button href={`${baseUrl}/admin/knowledge-gaps`} style={{ ...button, marginTop: 12 }}>
            Review gaps
          </Button>
        </Section>
      )}

      {/* New documents */}
      {new_document_titles.length > 0 && (
        <Section style={{ margin: "16px 0" }}>
          <Text style={sectionHeading}>New this week</Text>
          {new_document_titles.map((title, i) => (
            <Text key={i} style={listItem}>
              • {title}
            </Text>
          ))}
        </Section>
      )}

      {/* Action items */}
      {actions.length > 0 && (
        <Section style={callout}>
          <Text style={calloutHeading}>3 things to do this week</Text>
          {actions.map((action, i) => (
            <Text key={i} style={listItem}>
              {i + 1}. {action.text}{" "}
              {action.href && (
                <a href={`${baseUrl}${action.href}`} style={link}>
                  {action.cta}
                </a>
              )}
            </Text>
          ))}
        </Section>
      )}

      {/* Top intents */}
      {top_intents.length > 0 && (
        <Text style={muted}>
          What they used Contexo for:{" "}
          {top_intents
            .map((t) => `${t.intent} (${t.count})`)
            .join(" · ")}
        </Text>
      )}
    </EmailShell>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div
      style={{
        borderTop: "1px solid #e4e4e7",
        padding: "12px 0",
        display: "flex",
        justifyContent: "space-between",
      }}
    >
      <span style={{ color: "#3f3f46", fontSize: "14px" }}>{label}</span>
      <span style={{ color: "#09090b", fontSize: "14px", fontWeight: 600 }}>
        {value}
      </span>
    </div>
  );
}

function formatTimeSaved(hours: number, minutes: number): string {
  if (hours >= 1) return `${hours} hr`;
  if (minutes > 0) return `${minutes} min`;
  return "0 min";
}

function recommendActions({
  knowledge_gaps_count,
  low_confidence_count,
  ack_pending_count,
  negative_feedback_count,
  new_document_count,
}: {
  knowledge_gaps_count: number;
  low_confidence_count: number;
  ack_pending_count: number;
  negative_feedback_count: number;
  new_document_count: number;
}): { text: string; cta?: string; href?: string }[] {
  const out: { text: string; cta?: string; href?: string }[] = [];
  if (knowledge_gaps_count > 0) {
    out.push({
      text: `Fill ${knowledge_gaps_count} knowledge gap${knowledge_gaps_count === 1 ? "" : "s"} — AI drafts ready to review.`,
      cta: "Open knowledge gaps →",
      href: "/admin/knowledge-gaps",
    });
  }
  if (ack_pending_count > 0) {
    out.push({
      text: `Chase ${ack_pending_count} outstanding policy acknowledgement${ack_pending_count === 1 ? "" : "s"}.`,
      cta: "Open compliance →",
      href: "/admin/compliance",
    });
  }
  if (low_confidence_count >= 3 || negative_feedback_count >= 2) {
    out.push({
      text: `${low_confidence_count} low-confidence answer${low_confidence_count === 1 ? "" : "s"} — likely missing context.`,
      cta: "Open confidence →",
      href: "/admin/confidence",
    });
  }
  if (out.length < 3 && new_document_count === 0) {
    out.push({
      text: "No new documents this week — consider importing one to expand coverage.",
      cta: "Upload documents →",
      href: "/documents",
    });
  }
  return out.slice(0, 3);
}

export const weeklyDigestSubject = (props: WeeklyDigestEmailProps): string => {
  const hours = props.time_saved_hours ?? 0;
  const minutes = props.time_saved_minutes ?? 0;
  if (hours >= 1)
    return `${props.org_name} — ${hours} hr saved this week`;
  if (minutes > 0)
    return `${props.org_name} — ${minutes} min saved this week`;
  return `${props.org_name} — ${props.query_count} queries this week`;
};

export default WeeklyDigestEmail;

const hero: React.CSSProperties = {
  margin: "12px 0",
  padding: "20px 18px",
  borderRadius: "10px",
  border: "1px solid #c7d2fe",
  backgroundColor: "#eef2ff",
};

const heroLabel: React.CSSProperties = {
  color: "#4338ca",
  fontSize: "12px",
  fontWeight: 600,
  letterSpacing: "0.04em",
  margin: 0,
  textTransform: "uppercase",
};

const heroNumber: React.CSSProperties = {
  color: "#1e1b4b",
  fontSize: "32px",
  fontWeight: 700,
  lineHeight: "1.1",
  margin: "6px 0 4px 0",
};

const heroSub: React.CSSProperties = {
  color: "#4338ca",
  fontSize: "13px",
  margin: 0,
};

const callout: React.CSSProperties = {
  margin: "20px 0",
  padding: "14px 16px",
  borderRadius: "8px",
  border: "1px solid #e4e4e7",
  backgroundColor: "#fafafa",
};

const calloutHeading: React.CSSProperties = {
  color: "#18181b",
  fontSize: "14px",
  fontWeight: 600,
  margin: "0 0 8px 0",
};

const sectionHeading: React.CSSProperties = {
  color: "#18181b",
  fontSize: "13px",
  fontWeight: 600,
  letterSpacing: "0.02em",
  textTransform: "uppercase",
  margin: "0 0 6px 0",
};

const listItem: React.CSSProperties = {
  color: "#27272a",
  fontSize: "14px",
  lineHeight: "20px",
  margin: "4px 0",
};

const link: React.CSSProperties = {
  color: "#4338ca",
  textDecoration: "underline",
};
