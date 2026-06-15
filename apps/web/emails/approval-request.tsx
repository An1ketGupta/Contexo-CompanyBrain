import { Button, Hr, Row, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface ApprovalRequestEmailProps {
  approver_name: string;
  requester_name: string;
  channel_label: string;
  destination?: string | null;
  preview_text?: string | null;
  approve_url: string;
  reject_url: string;
  web_url: string;
  app_url: string;
}

export function ApprovalRequestEmail({
  approver_name,
  requester_name,
  channel_label,
  destination,
  preview_text,
  approve_url,
  reject_url,
  web_url,
}: ApprovalRequestEmailProps) {
  return (
    <EmailShell
      preview={`${requester_name} wants you to review a ${channel_label.toLowerCase()}`}
      heading={`${requester_name} needs your approval`}
    >
      <Text style={p}>
        Hi {approver_name}, {requester_name} has submitted a draft for your
        review.
      </Text>

      <Section style={metaBox}>
        <Row>
          <Text style={metaLabel}>Action</Text>
          <Text style={metaValue}>{channel_label}</Text>
        </Row>
        {destination ? (
          <Row>
            <Text style={metaLabel}>Destination</Text>
            <Text style={metaValue}>{destination}</Text>
          </Row>
        ) : null}
      </Section>

      {preview_text ? (
        <Section style={previewBox}>
          <Text style={previewLabel}>Draft preview</Text>
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

      <Hr style={hr} />

      <Text style={muted}>
        Approve & Reject use a one-click signed link that expires in 7 days.
        Prefer to open Company Brain?{" "}
        <a href={web_url} style={link}>
          View in app
        </a>
        .
      </Text>
    </EmailShell>
  );
}

export const approvalRequestSubject = (
  props: ApprovalRequestEmailProps,
): string =>
  `Approval needed: ${props.channel_label} from ${props.requester_name}`;

export default ApprovalRequestEmail;

const metaBox: React.CSSProperties = {
  border: "1px solid #e4e4e7",
  borderRadius: "6px",
  padding: "12px 14px",
  margin: "8px 0 16px 0",
};

const metaLabel: React.CSSProperties = {
  color: "#71717a",
  fontSize: "11px",
  fontWeight: 600,
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  margin: "0 0 2px 0",
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

const hr: React.CSSProperties = {
  borderColor: "#e4e4e7",
  margin: "20px 0",
};
