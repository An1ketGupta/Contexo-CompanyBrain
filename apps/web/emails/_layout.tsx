import {
  Body,
  Container,
  Head,
  Heading,
  Html,
  Preview,
  Section,
  Text,
} from "@react-email/components";

/**
 * Base shell for every transactional email. Keeps colors and spacing
 * consistent across templates and ensures we have a single place to wire
 * Outlook/Gmail dark-mode tweaks later.
 */
export function EmailShell({
  preview,
  heading,
  children,
}: {
  preview: string;
  heading?: string;
  children: React.ReactNode;
}) {
  return (
    <Html>
      <Head />
      <Preview>{preview}</Preview>
      <Body style={body}>
        <Container style={container}>
          <Section style={brand}>
            <Text style={brandText}>Nirnaya IQ</Text>
          </Section>
          {heading && <Heading style={h1}>{heading}</Heading>}
          {children}
          <Section style={footer}>
            <Text style={footerText}>
              You&apos;re receiving this email because you have an account with
              Nirnaya IQ. Reply to this email to talk to us.
            </Text>
          </Section>
        </Container>
      </Body>
    </Html>
  );
}

const body: React.CSSProperties = {
  backgroundColor: "#f4f4f5",
  fontFamily:
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, 'Helvetica Neue', sans-serif",
  margin: 0,
  padding: "24px 0",
};

const container: React.CSSProperties = {
  backgroundColor: "#ffffff",
  border: "1px solid #e4e4e7",
  borderRadius: "8px",
  margin: "0 auto",
  maxWidth: "560px",
  padding: "32px",
};

const brand: React.CSSProperties = {
  borderBottom: "1px solid #e4e4e7",
  paddingBottom: "16px",
  marginBottom: "24px",
};

const brandText: React.CSSProperties = {
  color: "#18181b",
  fontSize: "14px",
  fontWeight: 600,
  letterSpacing: "-0.01em",
  margin: 0,
};

const h1: React.CSSProperties = {
  color: "#09090b",
  fontSize: "20px",
  fontWeight: 600,
  letterSpacing: "-0.015em",
  margin: "0 0 16px 0",
};

const footer: React.CSSProperties = {
  borderTop: "1px solid #e4e4e7",
  marginTop: "32px",
  paddingTop: "16px",
};

const footerText: React.CSSProperties = {
  color: "#71717a",
  fontSize: "12px",
  lineHeight: "18px",
  margin: 0,
};

export const button: React.CSSProperties = {
  backgroundColor: "#18181b",
  borderRadius: "6px",
  color: "#fafafa",
  display: "inline-block",
  fontSize: "14px",
  fontWeight: 500,
  padding: "10px 18px",
  textDecoration: "none",
};

export const p: React.CSSProperties = {
  color: "#3f3f46",
  fontSize: "14px",
  lineHeight: "22px",
  margin: "0 0 16px 0",
};

export const muted: React.CSSProperties = {
  color: "#71717a",
  fontSize: "13px",
  lineHeight: "20px",
  margin: "0 0 16px 0",
};
