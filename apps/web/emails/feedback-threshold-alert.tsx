import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface FeedbackThresholdAlertExample {
  id: string;
  snippet: string;
  created_at: string;
}

export interface FeedbackThresholdAlertEmailProps {
  org_name: string;
  failure_reason: string;
  failure_reason_label: string;
  count: number;
  rollup_days: number;
  suggested_doc?: string | null;
  examples?: FeedbackThresholdAlertExample[];
  app_url: string;
}

export function FeedbackThresholdAlertEmail({
  org_name,
  failure_reason,
  failure_reason_label,
  count,
  rollup_days,
  suggested_doc,
  examples = [],
  app_url,
}: FeedbackThresholdAlertEmailProps) {
  const dashboardUrl = `${app_url.replace(/\/+$/, "")}/admin/analytics?period=7d`;
  const explainer = REASON_EXPLAINERS[failure_reason] ?? "";

  return (
    <EmailShell
      preview={`${count} '${failure_reason_label}' failures in ${rollup_days} days at ${org_name}`}
      heading={`${count} responses flagged as "${failure_reason_label}"`}
    >
      <Text style={p}>
        Over the last <strong>{rollup_days} days</strong>, users at{" "}
        <strong>{org_name}</strong> have thumbed-down{" "}
        <strong>{count} responses</strong> that Company Brain classified as{" "}
        <em>{failure_reason_label.toLowerCase()}</em>.
      </Text>

      {explainer ? <Text style={p}>{explainer}</Text> : null}

      {suggested_doc ? (
        <Section style={previewBox}>
          <Text style={previewLabel}>Suggested fix</Text>
          <Text style={previewText}>{suggested_doc}</Text>
        </Section>
      ) : null}

      {examples.length > 0 ? (
        <>
          <Text style={{ ...p, marginTop: 24, fontWeight: 600 }}>
            Example responses
          </Text>
          {examples.map((ex) => (
            <Section key={ex.id} style={exampleBox}>
              <Text style={exampleSnippet}>“{ex.snippet}”</Text>
              <Text style={exampleMeta}>
                {new Date(ex.created_at).toLocaleString()}
              </Text>
            </Section>
          ))}
        </>
      ) : null}

      <Section style={{ margin: "24px 0" }}>
        <Button href={dashboardUrl} style={button}>
          Open analytics dashboard
        </Button>
      </Section>

      <Text style={muted}>
        You&apos;re receiving this because you&apos;re an admin. Threshold
        alerts fire at most once per failure-mode per week.
      </Text>
    </EmailShell>
  );
}

export const feedbackThresholdAlertSubject = (
  props: FeedbackThresholdAlertEmailProps,
): string =>
  `${props.count} "${props.failure_reason_label}" failures this week — Company Brain`;

export default FeedbackThresholdAlertEmail;

const REASON_EXPLAINERS: Record<string, string> = {
  wrong_tone:
    "Responses don't match the tone your team expects — consider tightening your AI Instructions in Settings.",
  missing_context:
    "Common questions are landing without an authoritative document to anchor the answer. Adding the suggested doc usually fixes the pattern.",
  outdated_policy:
    "Responses are citing stale information. Check for older versions still tagged as authoritative and re-upload the current revision.",
  hallucination:
    "Responses are inventing information. Check if knowledge-gap alerts are accumulating in the same area — fill those gaps first.",
  wrong_format:
    "Output format doesn't match the use case. A template in Settings → Templates often resolves this.",
};

const previewBox: React.CSSProperties = {
  backgroundColor: "#fafafa",
  border: "1px solid #e4e4e7",
  borderRadius: "6px",
  padding: "16px",
  margin: "8px 0 16px 0",
};

const previewLabel: React.CSSProperties = {
  color: "#71717a",
  fontSize: "11px",
  fontWeight: 600,
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  margin: "0 0 8px 0",
};

const previewText: React.CSSProperties = {
  color: "#27272a",
  fontSize: "13px",
  lineHeight: "20px",
  margin: 0,
  whiteSpace: "pre-wrap",
};

const exampleBox: React.CSSProperties = {
  borderLeft: "3px solid #e4e4e7",
  padding: "4px 12px",
  margin: "8px 0",
};

const exampleSnippet: React.CSSProperties = {
  color: "#27272a",
  fontSize: "13px",
  lineHeight: "20px",
  margin: 0,
};

const exampleMeta: React.CSSProperties = {
  color: "#a1a1aa",
  fontSize: "11px",
  margin: "4px 0 0 0",
};
