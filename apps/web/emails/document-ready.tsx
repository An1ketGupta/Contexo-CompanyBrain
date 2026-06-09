import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface DocumentReadyEmailProps {
  doc_name: string;
  chunk_count: number;
  app_url: string;
}

export function DocumentReadyEmail({
  doc_name,
  chunk_count,
  app_url,
}: DocumentReadyEmailProps) {
  return (
    <EmailShell
      preview={`${doc_name} is ready to query`}
      heading="Your document is ready"
    >
      <Text style={p}>
        <strong>{doc_name}</strong> finished processing. It&apos;s indexed,
        embedded, and ready for your team to ask questions against.
      </Text>
      <Text style={muted}>
        {chunk_count} {chunk_count === 1 ? "chunk" : "chunks"} added to your
        knowledge base.
      </Text>
      <Section style={{ margin: "24px 0" }}>
        <Button href={`${app_url}/chat`} style={button}>
          Try it now
        </Button>
      </Section>
    </EmailShell>
  );
}

export const documentReadySubject = (props: DocumentReadyEmailProps): string =>
  `${props.doc_name} is ready`;

export default DocumentReadyEmail;
