import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface ApprovalResolvedEmailProps {
  requester_name: string;
  approver_name: string;
  channel_label: string;
  destination?: string | null;
  action: "approved" | "rejected";
  note?: string | null;
  web_url: string;
  app_url: string;
}

export function ApprovalResolvedEmail({
  requester_name,
  approver_name,
  channel_label,
  destination,
  action,
  note,
  web_url,
}: ApprovalResolvedEmailProps) {
  const approved = action === "approved";
  return (
    <EmailShell
      preview={`${approver_name} ${approved ? "approved" : "rejected"} your ${channel_label.toLowerCase()}`}
      heading={
        approved
          ? `${approver_name} approved your request`
          : `${approver_name} rejected your request`
      }
    >
      <Text style={p}>
        Hi {requester_name}, your draft has been{" "}
        <strong>{approved ? "approved" : "rejected"}</strong> by {approver_name}.
      </Text>

      {approved ? (
        <Text style={p}>
          We&apos;ve queued the action ({channel_label}
          {destination ? ` → ${destination}` : ""}) — it will be delivered
          shortly. You can track delivery status from the message in Company
          Brain.
        </Text>
      ) : (
        <Text style={p}>
          The {channel_label.toLowerCase()} was not executed. You can revise
          the draft and resubmit if needed.
        </Text>
      )}

      {note ? (
        <Section style={noteBox}>
          <Text style={noteLabel}>Note from {approver_name}</Text>
          <Text style={noteText}>{note}</Text>
        </Section>
      ) : null}

      <Section style={{ margin: "24px 0" }}>
        <Button href={web_url} style={button}>
          View in Company Brain
        </Button>
      </Section>

      <Text style={muted}>
        You can configure who approves what in Settings → Workflows.
      </Text>
    </EmailShell>
  );
}

export const approvalResolvedSubject = (
  props: ApprovalResolvedEmailProps,
): string =>
  props.action === "approved"
    ? `Approved: ${props.channel_label} by ${props.approver_name}`
    : `Rejected: ${props.channel_label} by ${props.approver_name}`;

export default ApprovalResolvedEmail;

const noteBox: React.CSSProperties = {
  backgroundColor: "#fafafa",
  border: "1px solid #e4e4e7",
  borderRadius: "6px",
  padding: "12px 14px",
  margin: "8px 0 16px 0",
};

const noteLabel: React.CSSProperties = {
  color: "#71717a",
  fontSize: "11px",
  fontWeight: 600,
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  margin: "0 0 4px 0",
};

const noteText: React.CSSProperties = {
  color: "#27272a",
  fontSize: "13px",
  lineHeight: "20px",
  margin: 0,
  whiteSpace: "pre-wrap",
};
