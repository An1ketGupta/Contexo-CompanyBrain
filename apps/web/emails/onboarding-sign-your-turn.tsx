import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingSignYourTurnEmailProps {
  recipient_name: string;
  document_label: string; // e.g. "Letter of Intent"
  signing_url: string;
  app_url: string;
}

export function OnboardingSignYourTurnEmail({
  recipient_name,
  document_label,
  signing_url,
}: OnboardingSignYourTurnEmailProps) {
  const first = recipient_name.split(" ")[0];

  return (
    <EmailShell
      preview={`It's your turn to sign the ${document_label}`}
      heading={`${first}, it's your turn to sign`}
    >
      <Text style={p}>
        The <strong>{document_label}</strong> is ready for your signature.
        Click below to review and sign.
      </Text>

      <Section style={{ margin: "20px 0" }}>
        <Button href={signing_url} style={button}>
          Review & sign
        </Button>
      </Section>

      <Text style={muted}>
        The link above takes you straight to the signing page — no account
        needed.
      </Text>

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />
      <Text style={p}>
        Once you sign, the next signer (if any) is notified automatically.
      </Text>
    </EmailShell>
  );
}

export const onboardingSignYourTurnSubject = (
  props: OnboardingSignYourTurnEmailProps,
): string => `It's your turn to sign — ${props.document_label}`;

export default OnboardingSignYourTurnEmail;
