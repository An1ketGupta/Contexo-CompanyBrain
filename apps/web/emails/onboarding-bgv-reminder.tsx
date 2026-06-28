import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingBgvReminderEmailProps {
  reference_name: string;
  candidate_name: string;
  company_name: string;
  form_url: string;
}

export function OnboardingBgvReminderEmail({
  reference_name,
  candidate_name,
  company_name,
  form_url,
}: OnboardingBgvReminderEmailProps) {
  return (
    <EmailShell
      preview={`Reminder — reference check for ${candidate_name}`}
      heading={`Quick reminder for ${reference_name}`}
    >
      <Text style={p}>
        Hi {reference_name} — just a friendly nudge. The reference form for{" "}
        <strong>{candidate_name}</strong> at <strong>{company_name}</strong> is
        still open and takes about 3 minutes to complete.
      </Text>

      <Section style={{ margin: "20px 0" }}>
        <Button href={form_url} style={button}>
          Open the form
        </Button>
      </Section>

      <Text style={muted}>
        If you&apos;d rather not respond, you can ignore this email and the
        link will expire automatically.
      </Text>
    </EmailShell>
  );
}

export const onboardingBgvReminderSubject = (
  props: OnboardingBgvReminderEmailProps,
): string => `Reminder: reference check for ${props.candidate_name}`;

export default OnboardingBgvReminderEmail;
