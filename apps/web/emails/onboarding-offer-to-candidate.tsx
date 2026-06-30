import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface OnboardingOfferToCandidateEmailProps {
  candidate_name: string;
  role_title: string;
  // Email-fallback path: signed URLs to the PDFs (when e-sign isn't on).
  appointment_letter_url?: string | null;
  nda_url?: string | null;
  // Embedded-signing path: one URL that wraps both documents.
  signing_url?: string | null;
  app_url: string;
}

export function OnboardingOfferToCandidateEmail({
  candidate_name,
  role_title,
  appointment_letter_url,
  nda_url,
  signing_url,
}: OnboardingOfferToCandidateEmailProps) {
  const first = candidate_name.split(" ")[0];
  const usingEsign = !!signing_url;

  return (
    <EmailShell
      preview={`Your Appointment Letter and NDA`}
      heading={`${first}, here are your Appointment Letter and NDA`}
    >
      <Text style={p}>
        Congratulations again on the <strong>{role_title}</strong> role.{" "}
        {usingEsign
          ? "Click the button below to review and sign both documents in one go."
          : "Please find the formal Appointment Letter and Non-Disclosure Agreement below. Review, sign both, and reply to this email with the signed copies."}
      </Text>

      <Section style={{ margin: "20px 0" }}>
        {usingEsign ? (
          <Button href={signing_url ?? "#"} style={button}>
            Review & sign
          </Button>
        ) : (
          <>
            {appointment_letter_url ? (
              <Button
                href={appointment_letter_url}
                style={{ ...button, marginRight: "10px" }}
              >
                Appointment Letter (PDF)
              </Button>
            ) : null}
            {nda_url ? (
              <Button
                href={nda_url}
                style={{
                  ...button,
                  backgroundColor: "#27272a",
                }}
              >
                NDA (PDF)
              </Button>
            ) : null}
          </>
        )}
      </Section>

      {usingEsign ? (
        <Text style={muted}>
          The link above takes you straight to the signing page — no account
          needed. The link is valid for one signing session; if it expires
          before you sign, reply to this email and we&apos;ll resend it.
        </Text>
      ) : null}

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />
      <Text style={p}>
        Next: we&apos;ll send you a few company policy documents to acknowledge
        and a personalised induction document before your first day.
      </Text>
    </EmailShell>
  );
}

export const onboardingOfferToCandidateSubject = (
  props: OnboardingOfferToCandidateEmailProps,
): string => `Your Appointment Letter and NDA — ${props.role_title}`;

export default OnboardingOfferToCandidateEmail;
