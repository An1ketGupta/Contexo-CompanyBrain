import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingDocumentsSentEmailProps {
  candidate_name: string;
  role_title: string;
  company_name: string;
  step_label: string;
  /** One entry per document in the step, signed URL where storage gave us one. */
  documents: Array<{ label: string; url: string | null }>;
  /** Set when the candidate still has to sign — the link goes to the envelope. */
  signing_url: string | null;
  app_url: string | null;
}

export function OnboardingDocumentsSentEmail({
  candidate_name,
  role_title,
  company_name,
  step_label,
  documents,
  signing_url,
}: OnboardingDocumentsSentEmailProps) {
  const first = candidate_name.split(" ")[0];
  const plural = documents.length === 1 ? "document" : "documents";
  return (
    <EmailShell
      preview={`${step_label} from ${company_name}`}
      heading={`${first}, here ${documents.length === 1 ? "is" : "are"} your ${plural}`}
    >
      <Text style={p}>
        {signing_url ? (
          <>
            Please review and sign the {plural} below for your{" "}
            <strong>{role_title}</strong> role at{" "}
            <strong>{company_name}</strong>.
          </>
        ) : (
          <>
            Sending on the {plural} below for your{" "}
            <strong>{role_title}</strong> role at{" "}
            <strong>{company_name}</strong>. Keep them somewhere safe — no
            action needed.
          </>
        )}
      </Text>

      {signing_url ? (
        <Section style={{ margin: "20px 0" }}>
          <Button href={signing_url} style={button}>
            Review and sign
          </Button>
        </Section>
      ) : (
        <Section style={{ margin: "16px 0" }}>
          {documents.map((doc) =>
            doc.url ? (
              <Text key={doc.label} style={{ ...p, margin: "6px 0" }}>
                <a href={doc.url} style={{ color: "#2563eb" }}>
                  {doc.label}
                </a>
              </Text>
            ) : (
              <Text key={doc.label} style={{ ...p, margin: "6px 0" }}>
                • {doc.label}
              </Text>
            ),
          )}
        </Section>
      )}

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />
      <Text style={muted}>
        Download links expire after a while. If one has stopped working, reply
        to this email and we&apos;ll send a fresh copy.
      </Text>
    </EmailShell>
  );
}

export const onboardingDocumentsSentSubject = (
  props: OnboardingDocumentsSentEmailProps,
): string => `${props.step_label} from ${props.company_name}`;

export default OnboardingDocumentsSentEmail;
