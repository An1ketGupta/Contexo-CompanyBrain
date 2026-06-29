import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingCandidateRefsReminderEmailProps {
  candidate_name: string;
  company_name: string;
  role_title: string;
  form_url: string;
}

export function OnboardingCandidateRefsReminderEmail({
  candidate_name,
  company_name,
  role_title,
  form_url,
}: OnboardingCandidateRefsReminderEmailProps) {
  const first = candidate_name.split(" ")[0];
  return (
    <EmailShell
      preview={`Reminder — submit your reference contacts for ${company_name}`}
      heading={`Hi ${first}, a quick reminder`}
    >
      <Text style={p}>
        We&apos;re still waiting on your background-check reference contacts
        for <strong>{role_title}</strong> at{" "}
        <strong>{company_name}</strong>. Once you submit them we can move on
        to your appointment letter and onboarding documents.
      </Text>

      <Section style={{ margin: "20px 0" }}>
        <Button href={form_url} style={button}>
          Submit reference contacts
        </Button>
      </Section>

      <Text style={muted}>
        The form takes a couple of minutes — the link is unique to you and
        expires soon.
      </Text>
    </EmailShell>
  );
}

export const onboardingCandidateRefsReminderSubject = (
  props: OnboardingCandidateRefsReminderEmailProps,
): string =>
  `Reminder: submit your reference contacts for ${props.company_name}`;

export default OnboardingCandidateRefsReminderEmail;
