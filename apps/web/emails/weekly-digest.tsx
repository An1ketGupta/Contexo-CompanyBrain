import { Section, Text } from "@react-email/components";
import { EmailShell, muted, p } from "./_layout";

export interface WeeklyDigestEmailProps {
  org_name: string;
  query_count: number;
  doc_count: number;
  top_doc?: string | null;
  active_users: number;
  app_url: string;
}

export function WeeklyDigestEmail({
  org_name,
  query_count,
  doc_count,
  top_doc,
  active_users,
}: WeeklyDigestEmailProps) {
  return (
    <EmailShell
      preview={`${query_count} queries this week in ${org_name}`}
      heading={`${org_name} — weekly summary`}
    >
      <Text style={p}>
        Here&apos;s what your team did with Company Brain this week.
      </Text>
      <Section style={{ margin: "24px 0" }}>
        <Stat label="Queries" value={query_count} />
        <Stat label="Documents in knowledge base" value={doc_count} />
        <Stat label="Active people" value={active_users} />
      </Section>
      {top_doc && (
        <Text style={muted}>
          Most-cited document this week: <strong>{top_doc}</strong>
        </Text>
      )}
    </EmailShell>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div
      style={{
        borderTop: "1px solid #e4e4e7",
        padding: "12px 0",
        display: "flex",
        justifyContent: "space-between",
      }}
    >
      <span style={{ color: "#3f3f46", fontSize: "14px" }}>{label}</span>
      <span style={{ color: "#09090b", fontSize: "14px", fontWeight: 600 }}>
        {value}
      </span>
    </div>
  );
}

export const weeklyDigestSubject = (props: WeeklyDigestEmailProps): string =>
  `${props.org_name} — ${props.query_count} queries this week`;

export default WeeklyDigestEmail;
