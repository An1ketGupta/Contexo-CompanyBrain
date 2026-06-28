import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingInductionReadyEmailProps {
  candidate_name: string;
  role_title: string;
  company_name: string;
  start_date: string;
  induction_signed_url: string | null;
  app_url: string;
}

export function OnboardingInductionReadyEmail({
  candidate_name,
  role_title,
  company_name,
  start_date,
  induction_signed_url,
}: OnboardingInductionReadyEmailProps) {
  const first = candidate_name.split(" ")[0];
  return (
    <EmailShell
      preview={`Welcome to ${company_name} — your induction document`}
      heading={`${first}, welcome to ${company_name}`}
    >
      <Text style={p}>
        Your personalised induction document is ready. We pulled together
        everything we think you&apos;ll find useful as a{" "}
        <strong>{role_title}</strong> — culture and values, who&apos;s on the
        team, the tools we use, processes, and where to learn more.
      </Text>
      <Text style={p}>
        Please read it end-to-end before <strong>{start_date}</strong>.
        Your manager will assume the contents are familiar in your first 1:1.
      </Text>

      {induction_signed_url ? (
        <Section style={{ margin: "20px 0" }}>
          <Button href={induction_signed_url} style={button}>
            Open my induction (PDF)
          </Button>
        </Section>
      ) : null}

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />
      <Text style={muted}>
        See you on {start_date}. We can&apos;t wait.
      </Text>
    </EmailShell>
  );
}

export const onboardingInductionReadySubject = (
  props: OnboardingInductionReadyEmailProps,
): string => `Welcome to ${props.company_name} — your induction document`;

export default OnboardingInductionReadyEmail;
