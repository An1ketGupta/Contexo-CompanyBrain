import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingEsignStalledEmailProps {
  candidate_name: string;
  role_title: string;
  hours_elapsed: number;
  run_url: string;
}

export function OnboardingEsignStalledEmail({
  candidate_name,
  role_title,
  hours_elapsed,
  run_url,
}: OnboardingEsignStalledEmailProps) {
  return (
    <EmailShell
      preview={`Signing for ${candidate_name} hasn't completed`}
      heading="LOI signing hasn't completed"
    >
      <Text style={p}>
        The Letter of Intent for <strong>{candidate_name}</strong> ({role_title})
        was sent for signature{" "}
        <strong>{hours_elapsed} hours ago</strong> but the envelope still
        hasn&apos;t been fully signed by both parties.
      </Text>
      <Text style={p}>
        The candidate may still be in the process of signing. You can check the
        status from the onboarding dashboard or cancel the run and restart the
        signing step.
      </Text>
      <Section style={{ margin: "20px 0" }}>
        <Button href={run_url} style={button}>
          View onboarding run
        </Button>
      </Section>
      <Text style={muted}>
        No action is required if the candidate is actively signing. If they
        haven&apos;t responded, open the run above, cancel it, and re-initiate
        the process.
      </Text>
    </EmailShell>
  );
}

export const onboardingEsignStalledSubject = (
  props: OnboardingEsignStalledEmailProps,
): string =>
  `Action needed: LOI signing for ${props.candidate_name} is still pending`;

export default OnboardingEsignStalledEmail;
