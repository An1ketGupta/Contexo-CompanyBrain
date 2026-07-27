import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface WeeklyBriefingEmailProps {
  recipient_name?: string;
  org_name: string;
  app_url: string;
  briefing_url: string;
  summary: string;
  body_markdown: string;
  period_key: string;
}

/**
 * Proactive Monday-morning briefing (Feature 2.2). Renders the LLM-composed
 * markdown body as paragraphs grouped by `## heading` blocks. The shell
 * mirrors weekly-digest so admins/users get a consistent visual identity.
 */
export function WeeklyBriefingEmail({
  recipient_name,
  org_name,
  briefing_url,
  summary,
  body_markdown,
}: WeeklyBriefingEmailProps) {
  const blocks = parseMarkdownBlocks(body_markdown);
  return (
    <EmailShell
      preview={summary || "Your weekly briefing is ready"}
      heading={
        recipient_name
          ? `Your week at ${org_name}, ${recipient_name}`
          : `Your week at ${org_name}`
      }
    >
      {blocks.map((block, i) => (
        <Section key={i} style={{ margin: "0 0 16px 0" }}>
          {block.heading ? (
            <Text style={sectionTitle}>{block.heading}</Text>
          ) : null}
          <Text style={p}>{block.body}</Text>
        </Section>
      ))}

      <Section style={{ margin: "24px 0 12px 0" }}>
        <Button href={briefing_url} style={button}>
          Open in Contexo
        </Button>
      </Section>

      <Text style={muted}>
        You can change when these arrive — or stop them — from{" "}
        <a href={`${briefing_url}#prefs`} style={{ color: "inherit" }}>
          briefing settings
        </a>
        .
      </Text>
    </EmailShell>
  );
}

export const weeklyBriefingSubject = (
  props: WeeklyBriefingEmailProps,
): string =>
  props.recipient_name
    ? `${props.recipient_name}, here's your week at ${props.org_name}`
    : `Your week at ${props.org_name}`;

export default WeeklyBriefingEmail;

/** Minimal markdown splitter — extracts `## Heading` followed by prose into
 * (heading, body) tuples. Anything before the first heading becomes a body
 * with no heading. Good enough for the four-section briefing format we
 * generate; not a general markdown engine. */
function parseMarkdownBlocks(
  md: string,
): { heading: string | null; body: string }[] {
  const out: { heading: string | null; body: string }[] = [];
  const lines = (md || "").split("\n");
  let current: { heading: string | null; body: string[] } = {
    heading: null,
    body: [],
  };
  for (const line of lines) {
    const m = /^##\s+(.+)$/.exec(line);
    if (m) {
      if (current.heading || current.body.length) {
        out.push({
          heading: current.heading,
          body: current.body.join(" ").trim(),
        });
      }
      current = { heading: m[1].trim(), body: [] };
    } else {
      current.body.push(line);
    }
  }
  if (current.heading || current.body.length) {
    out.push({ heading: current.heading, body: current.body.join(" ").trim() });
  }
  return out.filter((b) => b.heading || b.body);
}

const sectionTitle: React.CSSProperties = {
  color: "#0f172a",
  fontSize: "14px",
  fontWeight: 600,
  margin: "0 0 6px 0",
};
