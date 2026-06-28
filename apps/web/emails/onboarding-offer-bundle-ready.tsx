import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingOfferBundleReadyEmailProps {
  candidate_name: string;
  role_title: string;
  app_url: string;
  run_id: string;
}

export function OnboardingOfferBundleReadyEmail({
  candidate_name,
  role_title,
  app_url,
  run_id,
}: OnboardingOfferBundleReadyEmailProps) {
  return (
    <EmailShell
      preview={`Appointment letter + NDA ready for ${candidate_name}`}
      heading="The Appointment Letter and NDA are ready for your review"
    >
      <Text style={p}>
        Reference checks for <strong>{candidate_name}</strong> ({role_title})
        are complete. The Appointment Letter and NDA have been generated from
        your templates and are waiting for your one-click approval before they
        go to the candidate.
      </Text>

      <Section style={{ margin: "20px 0" }}>
        <Button
          href={`${app_url}/onboarding/${run_id}#offer-bundle`}
          style={button}
        >
          Review and approve
        </Button>
      </Section>

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />
      <Text style={muted}>
        Approving will email both documents to {candidate_name}. If anything
        needs editing, open the docs first — you can re-generate from the
        dashboard.
      </Text>
    </EmailShell>
  );
}

export const onboardingOfferBundleReadySubject = (
  props: OnboardingOfferBundleReadyEmailProps,
): string => `Appointment letter + NDA ready — ${props.candidate_name}`;

export default OnboardingOfferBundleReadyEmail;
