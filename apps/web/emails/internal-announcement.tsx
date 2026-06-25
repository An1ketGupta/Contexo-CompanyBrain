import { Section, Text } from "@react-email/components";
import { EmailShell, muted, p } from "./_layout";

/**
 * Internal announcement (Agent2 Day 4 #23).
 *
 * Pre-rendered HTML body (subset of safe tags) generated server-side from
 * the LLM-drafted Markdown. We don't run Markdown rendering inside the
 * React-Email render call because @react-email/render runs in the Next.js
 * route and is strict about side-effects; rendering Markdown server-side
 * in the Python worker gives us deterministic, sandbox-friendly HTML.
 *
 * `body_html` is wrapped in a styled <div> so paragraph spacing and link
 * colour match the rest of our email shell.
 */
export interface InternalAnnouncementEmailProps {
  org_name: string;
  sender_name?: string | null;
  app_url: string;
  /** Subject line — set by the admin on the announcement draft. */
  subject: string;
  /** Pre-rendered HTML (Markdown → HTML) of the announcement body. */
  body_html: string;
}

export function internalAnnouncementSubject(
  props: InternalAnnouncementEmailProps,
): string {
  return (props.subject || "").trim() || `Team announcement from ${props.org_name}`;
}

export function InternalAnnouncementEmail(
  props: InternalAnnouncementEmailProps,
) {
  const { org_name, sender_name, body_html } = props;
  return (
    <EmailShell
      preview={`Team announcement from ${org_name}`}
      heading={`Team announcement`}
    >
      {sender_name && (
        <Text style={muted}>
          From {sender_name} on behalf of {org_name}
        </Text>
      )}
      <Section>
        <div
          style={bodyContainer}
          dangerouslySetInnerHTML={{ __html: body_html }}
        />
      </Section>
      <Text style={muted}>
        Replies to this email reach {sender_name || "the sender"} directly.
      </Text>
    </EmailShell>
  );
}

const bodyContainer: React.CSSProperties = {
  ...p,
  color: "#3f3f46",
  fontSize: "14px",
  lineHeight: "22px",
};
