import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingOfferToCandidateEmailProps {
  candidate_name: string;
  role_title: string;
  appointment_letter_url: string | null;
  nda_url: string | null;
  app_url: string;
}

export function OnboardingOfferToCandidateEmail({
  candidate_name,
  role_title,
  appointment_letter_url,
  nda_url,
}: OnboardingOfferToCandidateEmailProps) {
  const first = candidate_name.split(" ")[0];
  return (
    <EmailShell
      preview={`Your Appointment Letter and NDA`}
      heading={`${first}, here are your Appointment Letter and NDA`}
    >
      <Text style={p}>
        Congratulations again on the <strong>{role_title}</strong> role. Please
        find the formal Appointment Letter and Non-Disclosure Agreement below.
        Review, sign both, and reply to this email with the signed copies.
      </Text>

      <Section style={{ margin: "20px 0" }}>
        {appointment_letter_url ? (
          <Button
            href={appointment_letter_url}
            style={{ ...button, marginRight: "10px" }}
          >
            Appointment Letter (PDF)
          </Button>
        ) : null}
        {nda_url ? (
          <Button
            href={nda_url}
            style={{
              ...button,
              backgroundColor: "#27272a",
            }}
          >
            NDA (PDF)
          </Button>
        ) : null}
      </Section>

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />
      <Text style={p}>
        Next: we&apos;ll send you a few company policy documents to acknowledge
        and a personalised induction document before your first day.
      </Text>
    </EmailShell>
  );
}

export const onboardingOfferToCandidateSubject = (
  props: OnboardingOfferToCandidateEmailProps,
): string => `Your Appointment Letter and NDA — ${props.role_title}`;

export default OnboardingOfferToCandidateEmail;
