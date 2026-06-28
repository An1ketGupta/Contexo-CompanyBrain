import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingBgvRequestEmailProps {
  reference_name: string;
  candidate_name: string;
  company_name: string;
  role_title: string;
  form_url: string;
}

export function OnboardingBgvRequestEmail({
  reference_name,
  candidate_name,
  company_name,
  role_title,
  form_url,
}: OnboardingBgvRequestEmailProps) {
  return (
    <EmailShell
      preview={`A reference check for ${candidate_name}`}
      heading={`Hi ${reference_name}, can you vouch for ${candidate_name}?`}
    >
      <Text style={p}>
        <strong>{candidate_name}</strong> has accepted an offer at{" "}
        <strong>{company_name}</strong> for the role of <strong>{role_title}</strong>
        , and listed you as a professional reference. We&apos;d love a quick
        word from you — it takes about <strong>3 minutes</strong>, no account
        needed.
      </Text>

      <Section style={{ margin: "20px 0" }}>
        <Button href={form_url} style={button}>
          Open the reference form
        </Button>
      </Section>

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />
      <Text style={muted}>
        This link is unique to you. Your responses are shared only with{" "}
        {company_name}&apos;s HR team. The link expires in 14 days.
      </Text>
    </EmailShell>
  );
}

export const onboardingBgvRequestSubject = (
  props: OnboardingBgvRequestEmailProps,
): string => `Reference check for ${props.candidate_name} — 3 minutes`;

export default OnboardingBgvRequestEmail;
