import { Button, Hr, Section, Text } from "@react-email/components";
import { EmailShell, button, muted, p } from "./_layout";

export interface RecruitingPublishedEmailProps {
  role_title: string;
  jd_excerpt: string;
  ats_url: string;
  app_url: string;
  requisition_id: string;
}

export function RecruitingPublishedEmail({
  role_title,
  jd_excerpt,
  ats_url,
  app_url,
  requisition_id,
}: RecruitingPublishedEmailProps) {
  return (
    <EmailShell
      preview={`${role_title} is live in your ATS`}
      heading="A new role just went live"
    >
      <Text style={p}>
        You&apos;ve been listed as the hiring manager on{" "}
        <strong>{role_title}</strong>. The job is live in your ATS and the
        sourcing kit is queued.
      </Text>

      <Section style={{ margin: "20px 0" }}>
        <Button href={ats_url} style={button}>
          Open in ATS
        </Button>
      </Section>

      <Hr style={{ borderColor: "#e4e4e7", margin: "24px 0" }} />

      <Text style={muted}>Job description preview</Text>
      <Text
        style={{
          ...p,
          backgroundColor: "#f4f4f5",
          borderRadius: "6px",
          padding: "12px 14px",
          whiteSpace: "pre-wrap",
          fontSize: "13px",
          lineHeight: "20px",
        }}
      >
        {jd_excerpt.slice(0, 800)}
        {jd_excerpt.length > 800 ? "…" : ""}
      </Text>

      <Text style={muted}>
        Manage the requisition, candidates, and pipeline from{" "}
        <a
          href={`${app_url}/recruiting/${requisition_id}`}
          style={{ color: "#18181b", textDecoration: "underline" }}
        >
          your recruiting workspace
        </a>
        .
      </Text>
    </EmailShell>
  );
}

export const recruitingPublishedSubject = (
  props: RecruitingPublishedEmailProps,
): string => `${props.role_title} just went live in your ATS`;

export default RecruitingPublishedEmail;
