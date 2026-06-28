import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingPoliciesPendingEmailProps {
  candidate_name: string;
  policy_count: number;
  app_url: string;
}

export function OnboardingPoliciesPendingEmail({
  candidate_name,
  policy_count,
  app_url,
}: OnboardingPoliciesPendingEmailProps) {
  const first = candidate_name.split(" ")[0];
  return (
    <EmailShell
      preview={`${policy_count} documents to acknowledge before Day 1`}
      heading={`${first}, a few documents to read before Day 1`}
    >
      <Text style={p}>
        We&apos;ve assigned <strong>{policy_count}</strong> company policy
        document{policy_count === 1 ? "" : "s"} for you to review and
        acknowledge before your first day. Most are 2-5 minutes each.
      </Text>

      <Section style={{ margin: "20px 0" }}>
        <Button href={`${app_url}/compliance/pending`} style={button}>
          Open my acknowledgements
        </Button>
      </Section>

      <Text style={muted}>
        Once you&apos;ve acknowledged all of them, we&apos;ll send across a
        personalised induction document with everything you need to know.
      </Text>
    </EmailShell>
  );
}

export const onboardingPoliciesPendingSubject = (
  props: OnboardingPoliciesPendingEmailProps,
): string =>
  `${props.policy_count} document${
    props.policy_count === 1 ? "" : "s"
  } to acknowledge before Day 1`;

export default OnboardingPoliciesPendingEmail;
