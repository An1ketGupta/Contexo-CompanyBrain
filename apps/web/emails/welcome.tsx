import { Button, Section, Text } from "@react-email/components";
import { EmailShell, button, p } from "./_layout";

export interface WelcomeEmailProps {
  first_name?: string | null;
  org_name: string;
  app_url: string;
}

export function WelcomeEmail({ first_name, org_name, app_url }: WelcomeEmailProps) {
  return (
    <EmailShell
      preview={`Welcome to Company Brain, ${first_name ?? "there"}`}
      heading={`Welcome${first_name ? `, ${first_name}` : ""}.`}
    >
      <Text style={p}>
        Your workspace <strong>{org_name}</strong> is live. Upload your first
        document and start asking questions in seconds.
      </Text>
      <Text style={p}>
        Company Brain works best when you give it real context — handbooks,
        product docs, meeting notes, brand guides. The more it knows about how
        your team works, the sharper its answers get.
      </Text>
      <Section style={{ margin: "24px 0" }}>
        <Button href={`${app_url}/documents`} style={button}>
          Upload your first document
        </Button>
      </Section>
    </EmailShell>
  );
}

export const welcomeSubject = (props: WelcomeEmailProps): string =>
  `Welcome to Company Brain${props.first_name ? `, ${props.first_name}` : ""}`;

export default WelcomeEmail;
