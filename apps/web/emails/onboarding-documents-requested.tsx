import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingDocumentsRequestedEmailProps {
  candidate_name: string;
  company_name: string;
  step_label: string;
  document_count: number;
  document_labels: string[];
  portal_url: string | null;
  app_url: string | null;
}

export function OnboardingDocumentsRequestedEmail({
  candidate_name,
  company_name,
  step_label,
  document_count,
  document_labels,
  portal_url,
}: OnboardingDocumentsRequestedEmailProps) {
  const first = candidate_name.split(" ")[0];
  const plural = document_count === 1 ? "document" : "documents";
  return (
    <EmailShell
      preview={`${company_name} needs ${document_count} ${plural} from you`}
      heading={`${first}, we need a few documents`}
    >
      <Text style={p}>
        To carry on with your onboarding at <strong>{company_name}</strong>,
        please upload the {plural} below. You can do it from your browser — no
        need to reply to this email with attachments.
      </Text>

      {document_labels.length ? (
        <Section style={{ margin: "16px 0" }}>
          <Text style={{ ...p, marginBottom: 8 }}>
            <strong>{step_label}</strong>
          </Text>
          {document_labels.map((label) => (
            <Text key={label} style={{ ...p, margin: "4px 0" }}>
              • {label}
            </Text>
          ))}
        </Section>
      ) : null}

      {portal_url ? (
        <Section style={{ margin: "20px 0" }}>
          <Button href={portal_url} style={button}>
            Upload my {plural}
          </Button>
        </Section>
      ) : null}

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />
      <Text style={muted}>
        A clear photo or scan is fine — PDF or image, whichever is easier. No
        account or password needed: the button above opens your checklist
        directly. The link is personal to you, so please don&apos;t forward it.
      </Text>
    </EmailShell>
  );
}

export const onboardingDocumentsRequestedSubject = (
  props: OnboardingDocumentsRequestedEmailProps,
): string =>
  `${props.company_name} needs ${props.document_count} ${
    props.document_count === 1 ? "document" : "documents"
  } from you`;

export default OnboardingDocumentsRequestedEmail;
