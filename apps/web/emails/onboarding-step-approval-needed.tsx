import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingStepApprovalNeededEmailProps {
  candidate_name: string;
  role_title: string;
  step_label: string;
  /** One line naming what came in — signed documents, N files, N referees. */
  summary: string;
  run_url: string | null;
  app_url: string | null;
}

export function OnboardingStepApprovalNeededEmail({
  candidate_name,
  role_title,
  step_label,
  summary,
  run_url,
}: OnboardingStepApprovalNeededEmailProps) {
  return (
    <EmailShell
      preview={`${candidate_name}: ${step_label} is ready for you to check`}
      heading={`${step_label} needs your sign-off`}
    >
      <Text style={p}>
        <strong>{candidate_name}</strong> ({role_title}) has done their part.
        Nothing moves on until you&rsquo;ve looked at it.
      </Text>

      <Section style={{ margin: "16px 0" }}>
        <Text style={{ ...p, margin: 0 }}>{summary}</Text>
      </Section>

      {run_url ? (
        <Section style={{ margin: "20px 0" }}>
          <Button href={run_url} style={button}>
            Open and review
          </Button>
        </Section>
      ) : null}

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />
      <Text style={muted}>
        Accepting it carries the onboarding on to the next step. Sending it back
        asks {candidate_name.split(" ")[0]} to redo just this part — the rest of
        the pipeline stays where it is.
      </Text>
    </EmailShell>
  );
}

export const onboardingStepApprovalNeededSubject = (
  props: OnboardingStepApprovalNeededEmailProps,
): string => `Check ${props.step_label} for ${props.candidate_name}`;

export default OnboardingStepApprovalNeededEmail;
