import { Button, Link, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface KnowledgeRefreshDoc {
  id: string;
  name: string;
  review_url: string;
}

export interface KnowledgeRefreshEmailProps {
  docs: KnowledgeRefreshDoc[];
  total_due: number;
  app_url: string;
}

export function KnowledgeRefreshEmail({
  docs,
  total_due,
  app_url,
}: KnowledgeRefreshEmailProps) {
  const shown = docs.length;
  const overflow = Math.max(0, total_due - shown);
  return (
    <EmailShell
      preview={`${total_due} document${total_due === 1 ? "" : "s"} due for review`}
      heading={`${total_due} document${total_due === 1 ? "" : "s"} in your knowledge base ${total_due === 1 ? "is" : "are"} due for review`}
    >
      <Text style={p}>
        Routine reviews keep your team&apos;s answers accurate. The
        documents below have hit the review cadence you set. Open each one,
        confirm it&apos;s still current, and click <strong>Mark as reviewed</strong>
        {" "}to reset the timer.
      </Text>

      <Section style={{ margin: "16px 0 8px" }}>
        {docs.map((doc) => (
          <Text key={doc.id} style={{ ...p, margin: "0 0 6px 0" }}>
            • <Link href={doc.review_url} style={{ color: "#0f172a", textDecoration: "underline" }}>{doc.name}</Link>
          </Text>
        ))}
        {overflow > 0 ? (
          <Text style={{ ...muted, margin: "8px 0 0 0" }}>
            and {overflow} more — see them all in the dashboard.
          </Text>
        ) : null}
      </Section>

      <Section style={{ margin: "24px 0" }}>
        <Button href={`${app_url.replace(/\/$/, "")}/documents?filter=overdue`} style={button}>
          Review documents
        </Button>
      </Section>

      <Text style={muted}>
        You&apos;re receiving this because you&apos;re an admin of a workspace
        with documents on a review cadence. We send this digest at most once
        per week.
      </Text>
    </EmailShell>
  );
}

export const knowledgeRefreshSubject = (
  props: KnowledgeRefreshEmailProps,
): string =>
  `${props.total_due} document${props.total_due === 1 ? "" : "s"} due for review`;

export default KnowledgeRefreshEmail;
