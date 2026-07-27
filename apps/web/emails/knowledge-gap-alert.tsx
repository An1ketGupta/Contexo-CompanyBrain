import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface KnowledgeGapAlertEmailProps {
  topic: string;
  count: number;
  stub_preview: string;
  review_url: string;
  app_url: string;
}

export function KnowledgeGapAlertEmail({
  topic,
  count,
  stub_preview,
  review_url,
}: KnowledgeGapAlertEmailProps) {
  return (
    <EmailShell
      preview={`"${topic}" asked ${count} times — review the AI-drafted stub`}
      heading={`Your team keeps asking about "${topic}"`}
    >
      <Text style={p}>
        We&apos;ve seen this topic come up{" "}
        <strong>{count} times in the last week</strong>, but your knowledge
        base doesn&apos;t cover it. Contexo has drafted a stub document
        below — review it, edit if needed, and approve to add it to your
        knowledge base.
      </Text>

      <Section style={previewBox}>
        <Text style={previewLabel}>AI-drafted stub preview</Text>
        <Text style={previewText}>{stub_preview}</Text>
      </Section>

      <Section style={{ margin: "24px 0" }}>
        <Button href={review_url} style={button}>
          Review draft
        </Button>
      </Section>

      <Text style={muted}>
        You&apos;re receiving this because you&apos;re an admin of this
        workspace. Knowledge-gap alerts fire once per topic per 24 hours —
        you won&apos;t get a flood of them.
      </Text>
    </EmailShell>
  );
}

export const knowledgeGapAlertSubject = (
  props: KnowledgeGapAlertEmailProps,
): string => `"${props.topic}" — ${props.count} unanswered queries this week`;

export default KnowledgeGapAlertEmail;

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
