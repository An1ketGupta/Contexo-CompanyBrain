import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingReferencesRequestedEmailProps {
  candidate_name: string;
  role_title: string;
  company_name: string;
  references_form_url: string | null;
  app_url: string | null;
}

export function OnboardingReferencesRequestedEmail({
  candidate_name,
  role_title,
  company_name,
  references_form_url,
}: OnboardingReferencesRequestedEmailProps) {
  const first = candidate_name.split(" ")[0];
  return (
    <EmailShell
      preview={`${company_name} needs your references`}
      heading={`${first}, please share your references`}
    >
      <Text style={p}>
        As part of your onboarding as a <strong>{role_title}</strong> at{" "}
        <strong>{company_name}</strong>, we need to hear from a couple of people
        you&apos;ve worked with.
      </Text>
      <Text style={p}>
        Use the form below to give us their name, email and how they know you.
        We&apos;ll write to them directly — you don&apos;t need to chase them.
      </Text>

      {references_form_url ? (
        <Section style={{ margin: "20px 0" }}>
          <Button href={references_form_url} style={button}>
            Submit my references
          </Button>
        </Section>
      ) : null}

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />
      <Text style={muted}>
        The link works for the next 14 days. If it lapses before you get to it,
        just reply to this email and we&apos;ll send a fresh one.
      </Text>
    </EmailShell>
  );
}

export const onboardingReferencesRequestedSubject = (
  props: OnboardingReferencesRequestedEmailProps,
): string => `${props.company_name} needs your references`;

export default OnboardingReferencesRequestedEmail;
