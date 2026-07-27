import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingWelcomeEmailProps {
  first_name: string;
  role: string;
  org_name: string;
  start_date?: string | null;
  notion_url?: string | null;
  plan_preview?: string | null;
  app_url: string;
}

export function OnboardingWelcomeEmail({
  first_name,
  role,
  org_name,
  start_date,
  notion_url,
  plan_preview,
  app_url,
}: OnboardingWelcomeEmailProps) {
  return (
    <EmailShell
      preview={`Welcome to ${org_name}, ${first_name}!`}
      heading={`Welcome to ${org_name}, ${first_name} 🎉`}
    >
      <Text style={p}>
        We&apos;re excited to have you on board as our new {role}
        {start_date ? `, starting ${start_date}` : ""}. Your personalised
        onboarding plan is ready — it covers your first day, first week, and
        30 / 60 / 90 day milestones.
      </Text>

      {plan_preview ? (
        <Section style={previewBox}>
          <Text style={previewLabel}>Your onboarding plan — preview</Text>
          <Text style={previewText}>{plan_preview}</Text>
        </Section>
      ) : null}

      <Section style={{ margin: "20px 0" }}>
        {notion_url ? (
          <Button href={notion_url} style={{ ...button, marginRight: 8 }}>
            Open onboarding plan
          </Button>
        ) : null}
        <Button href={`${app_url}`} style={secondaryButton}>
          Sign in to Contexo
        </Button>
      </Section>

      <Hr style={hr} />

      <Text style={muted}>
        You can talk to Contexo anytime — ask anything about company
        policies, projects, or who to reach out to. Your manager will be in
        touch to walk you through the first week.
      </Text>
    </EmailShell>
  );
}

export const onboardingWelcomeSubject = (
  props: OnboardingWelcomeEmailProps,
): string => `Welcome to ${props.org_name}, ${props.first_name}!`;

export default OnboardingWelcomeEmail;

const previewBox: React.CSSProperties = {
  backgroundColor: "#fafafa",
  border: "1px solid #e4e4e7",
  borderRadius: "6px",
  padding: "16px",
  margin: "8px 0 16px 0",
};

const previewLabel: React.CSSProperties = {
  color: "#71717a",
  fontSize: "11px",
  fontWeight: 600,
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  margin: "0 0 8px 0",
};

const previewText: React.CSSProperties = {
  color: "#27272a",
  fontSize: "13px",
  lineHeight: "20px",
  margin: 0,
  whiteSpace: "pre-wrap",
};

const secondaryButton: React.CSSProperties = {
  ...button,
  backgroundColor: "#fafafa",
  border: "1px solid #d4d4d8",
  color: "#18181b",
};

const hr: React.CSSProperties = {
  borderColor: "#e4e4e7",
  margin: "20px 0",
};
