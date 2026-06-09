import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, p } from "./_layout";

export interface QuotaExceededEmailProps {
  limit: number;
  plan: string;
  billing_url: string;
  resets_in_days: number;
}

export function QuotaExceededEmail({
  limit,
  plan,
  billing_url,
  resets_in_days,
}: QuotaExceededEmailProps) {
  return (
    <EmailShell
      preview="You've reached this month's query limit"
      heading="You've reached your query limit"
    >
      <Text style={p}>
        You&apos;ve used all <strong>{limit}</strong> AI tasks on your {plan}{" "}
        plan this month. Your team can&apos;t run new queries until your budget
        resets in {resets_in_days} {resets_in_days === 1 ? "day" : "days"} — or
        you upgrade.
      </Text>
      <Section style={{ margin: "24px 0" }}>
        <Button href={billing_url} style={button}>
          Upgrade plan
        </Button>
      </Section>
    </EmailShell>
  );
}

export const quotaExceededSubject = (_props: QuotaExceededEmailProps): string =>
  "You've reached your query limit";

export default QuotaExceededEmail;
