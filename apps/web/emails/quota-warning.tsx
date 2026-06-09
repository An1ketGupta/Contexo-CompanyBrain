import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface QuotaWarningEmailProps {
  used: number;
  limit: number;
  plan: string;
  billing_url: string;
}

export function QuotaWarningEmail({
  used,
  limit,
  plan,
  billing_url,
}: QuotaWarningEmailProps) {
  const percent = Math.min(100, Math.round((used / limit) * 100));
  return (
    <EmailShell
      preview={`You've used ${percent}% of your monthly queries`}
      heading={`You've used ${percent}% of your queries this month`}
    >
      <Text style={p}>
        <strong>
          {used} of {limit}
        </strong>{" "}
        AI tasks used on your {plan} plan. Your budget resets on the first of
        next month, but you can upgrade now to avoid interruption.
      </Text>
      <Section style={{ margin: "24px 0" }}>
        <Button href={billing_url} style={button}>
          Upgrade plan
        </Button>
      </Section>
      <Text style={muted}>
        You&apos;re seeing this because you crossed 80% of your plan&apos;s
        monthly task budget. We send this notice at most once per calendar month.
      </Text>
    </EmailShell>
  );
}

export const quotaWarningSubject = (props: QuotaWarningEmailProps): string =>
  `You've used ${Math.round((props.used / props.limit) * 100)}% of your monthly queries`;

export default QuotaWarningEmail;
