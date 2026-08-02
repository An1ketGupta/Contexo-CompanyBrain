import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingStepReviewReadyEmailProps {
  candidate_name: string;
  role_title: string;
  /** What the org called this step — the heading is built from it. */
  step_label: string;
  /** One entry per document generated for the step. */
  document_labels: string[];
  app_url: string;
  run_id: string;
}

export function OnboardingStepReviewReadyEmail({
  candidate_name,
  role_title,
  step_label,
  document_labels,
  app_url,
  run_id,
}: OnboardingStepReviewReadyEmailProps) {
  // Rendering is retried as transient on failure, so a payload missing this
  // would retry forever rather than fail once.
  const labels = document_labels ?? [];
  const plural = labels.length === 1 ? "document" : "documents";
  return (
    <EmailShell
      preview={`${step_label} ready for ${candidate_name}`}
      heading={`${step_label} is ready for your review`}
    >
      <Text style={p}>
        The {plural} for <strong>{step_label}</strong> {plural === "document" ? "has" : "have"}{" "}
        been generated for <strong>{candidate_name}</strong> ({role_title}) from
        your templates. Nothing goes to the candidate until you approve.
      </Text>

      {labels.length ? (
        <Section style={{ margin: "16px 0" }}>
          {labels.map((label) => (
            <Text key={label} style={{ ...p, margin: "6px 0" }}>
              • {label}
            </Text>
          ))}
        </Section>
      ) : null}

      <Section style={{ margin: "20px 0" }}>
        <Button href={`${app_url}/onboarding/${run_id}`} style={button}>
          Review and approve
        </Button>
      </Section>

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />
      <Text style={muted}>
        Approving moves the step on — depending on how it is set up, that means
        routing the {plural} for signature or emailing {plural === "document" ? "it" : "them"}{" "}
        to {candidate_name}. If anything needs editing, open the run and edit
        before you approve.
      </Text>
    </EmailShell>
  );
}

export const onboardingStepReviewReadySubject = (
  props: OnboardingStepReviewReadyEmailProps,
): string => `${props.step_label} ready to review — ${props.candidate_name}`;

export default OnboardingStepReviewReadyEmail;
