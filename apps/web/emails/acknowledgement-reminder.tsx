import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface AcknowledgementReminderEmailProps {
  first_name: string;
  org_name: string;
  pending_count: number;
  pending_docs: { name: string; document_id: string; url: string }[];
  app_url: string;
}

export function AcknowledgementReminderEmail({
  first_name,
  org_name,
  pending_count,
  pending_docs,
  app_url,
}: AcknowledgementReminderEmailProps) {
  const reviewUrl = `${app_url.replace(/\/$/, "")}/compliance/pending`;
  const docPreview = pending_docs.slice(0, 6);
  const hidden = Math.max(0, pending_docs.length - docPreview.length);
  return (
    <EmailShell
      preview={`${pending_count} policy ${pending_count === 1 ? "document" : "documents"} need your acknowledgement`}
      heading={
        pending_count === 1
          ? "A policy update needs your acknowledgement"
          : `${pending_count} policy updates need your acknowledgement`
      }
    >
      <Text style={p}>
        Hi {first_name}, {org_name} updated{" "}
        {pending_count === 1 ? "a policy" : `${pending_count} policies`} in
        Nirnaya IQ. Please review and confirm you&apos;ve read{" "}
        {pending_count === 1 ? "it" : "them"}.
      </Text>

      <Section style={list}>
        {docPreview.map((doc) => (
          <Text key={doc.document_id} style={listItem}>
            • {doc.name}
          </Text>
        ))}
        {hidden > 0 && (
          <Text style={listItem}>+ {hidden} more</Text>
        )}
      </Section>

      <Section style={{ margin: "20px 0" }}>
        <Button href={reviewUrl} style={button}>
          Review &amp; acknowledge
        </Button>
      </Section>

      <Text style={muted}>
        This takes less than a minute. You&apos;ll see the summary of what
        changed before you acknowledge.
      </Text>
    </EmailShell>
  );
}

export const acknowledgementReminderSubject = (
  props: AcknowledgementReminderEmailProps,
): string =>
  props.pending_count === 1
    ? `Please acknowledge a policy update — ${props.org_name}`
    : `Please acknowledge ${props.pending_count} policy updates — ${props.org_name}`;

export default AcknowledgementReminderEmail;

const list: React.CSSProperties = {
  border: "1px solid #e4e4e7",
  borderRadius: "6px",
  padding: "12px 16px",
  margin: "12px 0 4px 0",
};

const listItem: React.CSSProperties = {
  color: "#18181b",
  fontSize: "14px",
  lineHeight: "20px",
  margin: "4px 0",
};
