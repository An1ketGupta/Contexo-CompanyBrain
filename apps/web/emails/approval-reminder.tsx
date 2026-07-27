import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface ApprovalReminderEmailProps {
  approver_name: string;
  requester_name: string;
  channel_label: string;
  destination?: string | null;
  preview_text?: string | null;
  approve_url: string;
  reject_url: string;
  web_url: string;
  app_url: string;
  waiting_since: string;
}

export function ApprovalReminderEmail({
  approver_name,
  requester_name,
  channel_label,
  destination,
  preview_text,
  approve_url,
  reject_url,
  web_url,
}: ApprovalReminderEmailProps) {
  return (
    <EmailShell
      preview={`Still waiting — ${requester_name}'s ${channel_label.toLowerCase()} needs review`}
      heading={`Reminder: ${requester_name} is still waiting`}
    >
      <Text style={p}>
        Hi {approver_name}, this approval has been pending for 24 hours.
      </Text>

      <Section style={metaBox}>
        <Text style={metaValue}>
          <strong>{channel_label}</strong>
          {destination ? ` → ${destination}` : ""}
        </Text>
      </Section>

      {preview_text ? (
        <Section style={previewBox}>
          <Text style={previewText}>{preview_text}</Text>
        </Section>
      ) : null}

      <Section style={{ margin: "20px 0" }}>
        <Button href={approve_url} style={{ ...button, marginRight: 8 }}>
          Approve
        </Button>
        <Button href={reject_url} style={rejectButton}>
          Reject
        </Button>
      </Section>

      <Text style={muted}>
        Prefer to open the app?{" "}
        <a href={web_url} style={link}>
          Review in Contexo
        </a>
        .
      </Text>
    </EmailShell>
  );
}

export const approvalReminderSubject = (
  props: ApprovalReminderEmailProps,
): string => `Reminder: ${props.requester_name} is still waiting for approval`;

export default ApprovalReminderEmail;

const metaBox: React.CSSProperties = {
  border: "1px solid #e4e4e7",
  borderRadius: "6px",
  padding: "12px 14px",
  margin: "8px 0 12px 0",
};

const metaValue: React.CSSProperties = {
  color: "#18181b",
  fontSize: "13px",
  margin: 0,
};

const previewBox: React.CSSProperties = {
  backgroundColor: "#fafafa",
  border: "1px solid #e4e4e7",
  borderRadius: "6px",
  padding: "16px",
  margin: "8px 0 16px 0",
};

const previewText: React.CSSProperties = {
  color: "#27272a",
  fontSize: "13px",
  lineHeight: "20px",
  margin: 0,
  whiteSpace: "pre-wrap",
};

const rejectButton: React.CSSProperties = {
  ...button,
  backgroundColor: "#fafafa",
  border: "1px solid #d4d4d8",
  color: "#18181b",
};

const link: React.CSSProperties = {
  color: "#18181b",
  textDecoration: "underline",
};
