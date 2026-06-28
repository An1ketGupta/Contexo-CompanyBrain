import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingLoiReadyEmailProps {
  candidate_name: string;
  role_title: string;
  ctc: string;
  start_date: string;
  loi_signed_url: string | null;
  app_url: string;
  run_id: string;
}

export function OnboardingLoiReadyEmail({
  candidate_name,
  role_title,
  ctc,
  start_date,
  loi_signed_url,
  app_url,
  run_id,
}: OnboardingLoiReadyEmailProps) {
  return (
    <EmailShell
      preview={`LOI ready for ${candidate_name}`}
      heading="The Letter of Intent is ready for your signature"
    >
      <Text style={p}>
        We just generated the Letter of Intent for{" "}
        <strong>{candidate_name}</strong> ({role_title}). Download it, sign it,
        scan it, and upload the signed copy from the onboarding dashboard — the
        agent will email it to the candidate from there.
      </Text>

      <Section style={{ margin: "20px 0" }}>
        {loi_signed_url ? (
          <Button href={loi_signed_url} style={button}>
            Download LOI (PDF)
          </Button>
        ) : null}
      </Section>

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />

      <Text style={muted}>
        <strong>Role:</strong> {role_title}
        <br />
        <strong>CTC:</strong> {ctc}
        <br />
        <strong>Start date:</strong> {start_date}
      </Text>

      <Text style={muted}>
        Open the onboarding run to upload the signed copy:{" "}
        <a
          href={`${app_url}/onboarding/${run_id}`}
          style={{ color: "#18181b", textDecoration: "underline" }}
        >
          {app_url}/onboarding/{run_id}
        </a>
      </Text>
    </EmailShell>
  );
}

export const onboardingLoiReadySubject = (
  props: OnboardingLoiReadyEmailProps,
): string => `LOI ready to sign — ${props.candidate_name}`;

export default OnboardingLoiReadyEmail;
