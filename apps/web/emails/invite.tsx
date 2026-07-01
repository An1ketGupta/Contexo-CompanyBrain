import { Button, Link, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface InviteEmailProps {
  org_name: string;
  inviter_name?: string | null;
  role: "admin" | "member";
  accept_url: string;
  expires_in_days?: number;
}

export function InviteEmail({
  org_name,
  inviter_name,
  role,
  accept_url,
  expires_in_days = 7,
}: InviteEmailProps) {
  const subject = inviter_name
    ? `${inviter_name} invited you to ${org_name} on Nirnaya IQ`
    : `You've been invited to ${org_name} on Nirnaya IQ`;

  return (
    <EmailShell preview={subject} heading={`Join ${org_name} on Nirnaya IQ`}>
      <Text style={p}>
        {inviter_name
          ? `${inviter_name} added you to ${org_name} as a${role === "admin" ? "n admin" : " member"}.`
          : `You've been added to ${org_name} as a${role === "admin" ? "n admin" : " member"}.`}
      </Text>
      <Text style={p}>
        Nirnaya IQ is your team&apos;s knowledge base — everything you know
        about your business, queryable in plain English.
      </Text>
      <Section style={{ margin: "24px 0" }}>
        <Button href={accept_url} style={button}>
          Accept invitation
        </Button>
      </Section>
      <Text style={muted}>
        This link expires in {expires_in_days} days. If the button doesn&apos;t
        work, copy and paste this URL into your browser:
      </Text>
      <Text style={{ ...muted, wordBreak: "break-all" }}>
        <Link href={accept_url} style={{ color: "#3f3f46" }}>
          {accept_url}
        </Link>
      </Text>
    </EmailShell>
  );
}

export const inviteSubject = (props: InviteEmailProps): string =>
  props.inviter_name
    ? `${props.inviter_name} invited you to ${props.org_name} on Nirnaya IQ`
    : `You've been invited to ${props.org_name} on Nirnaya IQ`;

export default InviteEmail;
