import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingStepRejectedEmailProps {
  candidate_name: string;
  step_label: string;
  /** What has to happen, in the recipient's terms — "2 documents need re-uploading". */
  what_happened: string;
  /** HR's own words. The only instruction the candidate gets. */
  reason: string;
  action_url: string | null;
  action_label: string | null;
}

export function OnboardingStepRejectedEmail({
  candidate_name,
  step_label,
  what_happened,
  reason,
  action_url,
  action_label,
}: OnboardingStepRejectedEmailProps) {
  const first = candidate_name.split(" ")[0];
  return (
    <EmailShell
      preview={`${step_label}: ${what_happened}`}
      heading={`${first}, one thing to redo`}
    >
      <Text style={p}>
        We&rsquo;ve looked at <strong>{step_label}</strong> and{" "}
        {what_happened}. Everything else you&rsquo;ve sent is fine — this is the
        only outstanding item.
      </Text>

      <Section
        style={{
          margin: "16px 0",
          padding: "12px 16px",
          borderLeft: "3px solid #e4e4e7",
        }}
      >
        <Text style={{ ...p, margin: 0 }}>{reason}</Text>
      </Section>

      {action_url && action_label ? (
        <Section style={{ margin: "20px 0" }}>
          <Button href={action_url} style={button}>
            {action_label}
          </Button>
        </Section>
      ) : null}

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />
      <Text style={muted}>
        Your start date and everything already agreed are unaffected. If the
        note above isn&rsquo;t clear, reply to this email and your HR contact
        will explain.
      </Text>
    </EmailShell>
  );
}

export const onboardingStepRejectedSubject = (
  props: OnboardingStepRejectedEmailProps,
): string => `Action needed: ${props.step_label}`;

export default OnboardingStepRejectedEmail;
