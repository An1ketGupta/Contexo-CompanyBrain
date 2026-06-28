import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingLoiToCandidateEmailProps {
  candidate_name: string;
  role_title: string;
  company_name: string;
  start_date: string;
  loi_signed_url: string | null;
  app_url: string;
}

export function OnboardingLoiToCandidateEmail({
  candidate_name,
  role_title,
  company_name,
  start_date,
  loi_signed_url,
}: OnboardingLoiToCandidateEmailProps) {
  const first = candidate_name.split(" ")[0];
  return (
    <EmailShell
      preview={`Your Letter of Intent from ${company_name}`}
      heading={`Welcome, ${first} — here's your Letter of Intent`}
    >
      <Text style={p}>
        We&apos;re excited to have you joining <strong>{company_name}</strong>{" "}
        as <strong>{role_title}</strong> starting <strong>{start_date}</strong>.
      </Text>
      <Text style={p}>
        Please find your signed Letter of Intent attached. Hold on to this — the
        Appointment Letter and other formal documents will follow once we
        complete a quick background reference check.
      </Text>

      {loi_signed_url ? (
        <Section style={{ margin: "20px 0" }}>
          <Button href={loi_signed_url} style={button}>
            Download Letter of Intent
          </Button>
        </Section>
      ) : null}

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />
      <Text style={muted}>
        We&apos;ll be in touch over the next few days with the reference check,
        appointment letter, and induction details.
      </Text>
    </EmailShell>
  );
}

export const onboardingLoiToCandidateSubject = (
  props: OnboardingLoiToCandidateEmailProps,
): string => `Your Letter of Intent — ${props.company_name}`;

export default OnboardingLoiToCandidateEmail;
