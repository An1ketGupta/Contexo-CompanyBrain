import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

/**
 * Scheduled usage summary report (V5 #98).
 *
 * Sent on a user-configured cadence (daily/weekly/monthly) to admin-selected
 * recipients. Mirrors the in-app analytics page so what arrives in the inbox
 * matches what they'd see at /admin/analytics.
 */
export interface ScheduledUsageSummaryEmailProps {
  org_name: string;
  frequency: "daily" | "weekly" | "monthly";
  window_days: number;
  app_url: string;

  query_count: number;
  active_users: number;
  time_saved_hours: number;
  time_saved_minutes: number;
  positive_feedback_count: number;
  negative_feedback_count: number;
  low_confidence_count: number;
  new_document_count: number;
  new_document_titles: string[];
  top_intents: { intent: string; count: number }[];
}

const FREQUENCY_LABEL: Record<ScheduledUsageSummaryEmailProps["frequency"], string> = {
  daily: "yesterday",
  weekly: "last 7 days",
  monthly: "last 30 days",
};

export function scheduledUsageSummarySubject(
  props: ScheduledUsageSummaryEmailProps,
): string {
  const label =
    props.frequency === "daily"
      ? "Daily"
      : props.frequency === "weekly"
        ? "Weekly"
        : "Monthly";
  return `${label} usage summary — ${props.org_name}`;
}

export function ScheduledUsageSummaryEmail(props: ScheduledUsageSummaryEmailProps) {
  const {
    org_name,
    frequency,
    window_days,
    app_url,
    query_count,
    active_users,
    time_saved_hours,
    time_saved_minutes,
    positive_feedback_count,
    negative_feedback_count,
    low_confidence_count,
    new_document_count,
    new_document_titles,
    top_intents,
  } = props;

  const base = app_url.replace(/\/$/, "");
  const windowLabel = FREQUENCY_LABEL[frequency] ?? `last ${window_days} days`;
  const heroValue = formatTimeSaved(time_saved_hours, time_saved_minutes);

  return (
    <EmailShell
      preview={`${query_count} queries · ${heroValue} saved`}
      heading={`${org_name} — usage summary`}
    >
      <Text style={muted}>For the {windowLabel}.</Text>

      {/* Hero */}
      <Section style={heroSection}>
        <Text style={heroLabel}>Time saved</Text>
        <Text style={heroNumber}>{heroValue}</Text>
        <Text style={heroSub}>
          {query_count.toLocaleString()} queries · {active_users} active{" "}
          {active_users === 1 ? "user" : "users"}
        </Text>
      </Section>

      {/* Quality */}
      <Section>
        <Text style={sectionHeading}>Quality signals</Text>
        <Text style={p}>
          {positive_feedback_count} positive · {negative_feedback_count} negative ·{" "}
          {low_confidence_count} low-confidence
        </Text>
      </Section>

      {/* New docs */}
      {new_document_count > 0 && (
        <Section>
          <Text style={sectionHeading}>New documents ({new_document_count})</Text>
          {new_document_titles.slice(0, 5).map((t) => (
            <Text key={t} style={listItem}>
              · {t}
            </Text>
          ))}
        </Section>
      )}

      {/* Top intents */}
      {top_intents.length > 0 && (
        <Section>
          <Text style={sectionHeading}>What the team used Brain for</Text>
          {top_intents.map((i) => (
            <Text key={i.intent} style={listItem}>
              · {i.intent.replace(/_/g, " ")} ({i.count})
            </Text>
          ))}
        </Section>
      )}

      <Section style={{ marginTop: "24px" }}>
        <Button href={`${base}/admin/analytics`} style={button}>
          Open analytics dashboard
        </Button>
      </Section>
      <Text style={muted}>
        You&apos;re receiving this because an admin scheduled this report. Manage at{" "}
        <a href={`${base}/settings/reports`} style={{ color: "#3f3f46" }}>
          settings → reports
        </a>
        .
      </Text>
    </EmailShell>
  );
}

function formatTimeSaved(hours: number, minutes: number): string {
  if (hours >= 1) return `${hours.toLocaleString()} hr`;
  if (minutes >= 1) return `${minutes.toLocaleString()} min`;
  return "—";
}

const heroSection: React.CSSProperties = {
  backgroundColor: "#fafafa",
  border: "1px solid #e4e4e7",
  borderRadius: "8px",
  margin: "16px 0 24px 0",
  padding: "20px",
  textAlign: "center",
};

const heroLabel: React.CSSProperties = {
  color: "#71717a",
  fontSize: "12px",
  fontWeight: 500,
  letterSpacing: "0.05em",
  margin: 0,
  textTransform: "uppercase",
};

const heroNumber: React.CSSProperties = {
  color: "#09090b",
  fontSize: "32px",
  fontWeight: 700,
  letterSpacing: "-0.02em",
  margin: "8px 0 4px 0",
};

const heroSub: React.CSSProperties = {
  color: "#52525b",
  fontSize: "13px",
  margin: 0,
};

const sectionHeading: React.CSSProperties = {
  color: "#09090b",
  fontSize: "14px",
  fontWeight: 600,
  margin: "16px 0 8px 0",
};

const listItem: React.CSSProperties = {
  color: "#3f3f46",
  fontSize: "13px",
  lineHeight: "20px",
  margin: "0 0 4px 0",
};
