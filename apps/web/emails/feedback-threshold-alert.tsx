import { Button, Link, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface FeedbackThresholdAlertExample {
  id: string;
  // Optional so older queued events (pre-deep-link rollout) still render.
  // We just degrade to a non-clickable snippet when absent.
  conversation_id?: string | null;
  snippet: string;
  created_at: string;
}

export interface FeedbackThresholdAlertEmailProps {
  org_name: string;
  failure_reason: string;
  failure_reason_label: string;
  count: number;
  rollup_days: number;
  examples?: FeedbackThresholdAlertExample[];
  app_url: string;
}

export function FeedbackThresholdAlertEmail({
  org_name,
  failure_reason,
  failure_reason_label,
  count,
  rollup_days,
  examples = [],
  app_url,
}: FeedbackThresholdAlertEmailProps) {
  const base = app_url.replace(/\/+$/, "");
  // Deep link to the filtered feedback dashboard. `since` keeps the user in
  // the same rolling window the email reflects so the row counts agree.
  const feedbackUrl = `${base}/admin/feedback?reason=${encodeURIComponent(
    failure_reason,
  )}&days=${rollup_days}`;
  const explainer = REASON_EXPLAINERS[failure_reason] ?? "";

  return (
    <EmailShell
      preview={`${count} '${failure_reason_label}' failures in ${rollup_days} days at ${org_name}`}
      heading={`${count} responses flagged as "${failure_reason_label}"`}
    >
      <Text style={p}>
        Over the last <strong>{rollup_days} days</strong>, users at{" "}
        <strong>{org_name}</strong> have thumbed-down{" "}
        <strong>{count} responses</strong> that Nirnaya IQ classified as{" "}
        <em>{failure_reason_label.toLowerCase()}</em>.
      </Text>

      {explainer ? <Text style={p}>{explainer}</Text> : null}

      {examples.length > 0 ? (
        <>
          <Text style={{ ...p, marginTop: 24, fontWeight: 600 }}>
            Example responses
          </Text>
          {examples.map((ex) => {
            const chatUrl = ex.conversation_id
              ? `${base}/chat/${ex.conversation_id}#m-${ex.id}`
              : null;
            return (
              <Section key={ex.id} style={exampleBox}>
                <Text style={exampleSnippet}>“{ex.snippet}”</Text>
                <Text style={exampleMeta}>
                  {new Date(ex.created_at).toLocaleString()}
                  {chatUrl ? (
                    <>
                      {" · "}
                      <Link href={chatUrl} style={exampleLink}>
                        Open in chat
                      </Link>
                    </>
                  ) : null}
                </Text>
              </Section>
            );
          })}
        </>
      ) : null}

      <Section style={{ margin: "24px 0" }}>
        <Button href={feedbackUrl} style={button}>
          Review all {count} flagged responses
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
  `${props.count} "${props.failure_reason_label}" failures this week — Nirnaya IQ`;

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

const exampleLink: React.CSSProperties = {
  color: "#3f3f46",
  textDecoration: "underline",
};
