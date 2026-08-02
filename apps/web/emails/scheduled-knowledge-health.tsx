import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

/**
 * Knowledge base health report (V5 #98).
 *
 * Snapshot of the corpus: top-cited docs (working knowledge) and stale docs
 * (zero citations & older than 30d).
 */
export interface ScheduledKnowledgeHealthEmailProps {
  org_name: string;
  app_url: string;
  total_docs: number;
  top_documents: { name: string; citations: number }[];
  stale_documents: { name: string }[];
  stale_count: number;
}

export function scheduledKnowledgeHealthSubject(
  props: ScheduledKnowledgeHealthEmailProps,
): string {
  return `Knowledge base health — ${props.org_name}`;
}

export function ScheduledKnowledgeHealthEmail(
  props: ScheduledKnowledgeHealthEmailProps,
) {
  const {
    org_name,
    app_url,
    total_docs,
    top_documents,
    stale_documents,
    stale_count,
  } = props;

  const base = app_url.replace(/\/$/, "");

  return (
    <EmailShell
      preview={`${total_docs} docs in your knowledge base`}
      heading={`${org_name} — knowledge health`}
    >
      <Text style={p}>
        Snapshot of your knowledge base. {total_docs.toLocaleString()} total documents.
      </Text>

      {top_documents.length > 0 && (
        <Section>
          <Text style={sectionHeading}>Most-cited documents</Text>
          {top_documents.map((d) => (
            <Text key={d.name} style={listItem}>
              · {d.name} <span style={countStyle}>({d.citations})</span>
            </Text>
          ))}
        </Section>
      )}

      {stale_count > 0 && (
        <Section>
          <Text style={sectionHeading}>Possibly stale ({stale_count})</Text>
          <Text style={muted}>
            Documents older than 30 days that have never been cited in a chat answer.
          </Text>
          {stale_documents.slice(0, 5).map((d) => (
            <Text key={d.name} style={listItem}>
              · {d.name}
            </Text>
          ))}
        </Section>
      )}

      <Section style={{ marginTop: "24px" }}>
        <Button href={`${base}/documents`} style={button}>
          Open your documents
        </Button>
      </Section>
      <Text style={muted}>
        Manage scheduled reports at{" "}
        <a href={`${base}/settings/reports`} style={{ color: "#3f3f46" }}>
          settings → reports
        </a>
        .
      </Text>
    </EmailShell>
  );
}

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

const countStyle: React.CSSProperties = {
  color: "#71717a",
  fontSize: "12px",
};
