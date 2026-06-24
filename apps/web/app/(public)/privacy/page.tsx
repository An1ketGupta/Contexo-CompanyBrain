import type { Metadata } from "next";

// IMPORTANT — LEGAL_PLACEHOLDER
// ─────────────────────────────────────────────────────────────────────────
// This page exists so the product is structurally compliant: routes, links,
// and footer references all resolve. The narrative below is a starter draft
// that mirrors the product's actual data flows; it is NOT lawyer-reviewed.
//
// Before commercial launch, replace each <section> body with copy from
// either (a) a qualified lawyer or (b) a templated service such as Termly /
// Iubenda configured to your jurisdiction and data-processor list. Leave
// the section structure intact unless your reviewer says otherwise — every
// section here covers a category of data we genuinely handle.
// ─────────────────────────────────────────────────────────────────────────

export const metadata: Metadata = {
  title: "Privacy Policy",
  description:
    "How NirnayaIQ collects, processes, and protects your personal and workspace data.",
};

const EFFECTIVE_DATE = "Pending — set once final copy is reviewed.";

export default function PrivacyPolicyPage() {
  return (
    <article className="mx-auto max-w-3xl px-4 py-12 sm:py-16">
      <header className="mb-8 border-b border-border pb-6">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">
          Legal
        </p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight text-foreground">
          Privacy Policy
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Effective date: {EFFECTIVE_DATE}
        </p>
      </header>

      <PlaceholderNotice />

      <div className="space-y-10 leading-relaxed text-foreground">
        <Section title="1. Who we are">
          <p>
            NirnayaIQ (&ldquo;<strong>we</strong>&rdquo;, &ldquo;<strong>us</strong>
            &rdquo;) operates a multi-tenant SaaS work execution platform that
            ingests workspace knowledge bases and assists employees with AI-powered
            outputs. This Privacy Policy explains what personal data we process,
            why, and the choices you have.
          </p>
          <p>
            For any privacy question, contact{" "}
            <a className="underline" href="mailto:privacy@nirnayaiq.com">
              privacy@nirnayaiq.com
            </a>
            .
          </p>
        </Section>

        <Section title="2. Data we collect">
          <ul className="list-disc space-y-2 pl-5">
            <li>
              <strong>Account data</strong> — name, work email, password
              hash (held by Supabase Auth), display name, role within your
              workspace.
            </li>
            <li>
              <strong>Workspace content</strong> — documents you upload, files
              synced from connected integrations (Google Drive, Notion,
              Slack, Gmail, OneDrive, Confluence, GitHub, Dropbox, email
              forwarding), and any text you ask the assistant about.
            </li>
            <li>
              <strong>Usage data</strong> — chat messages, query logs, feedback
              ratings, latency and model-call metrics. Used to operate the
              product and improve retrieval quality.
            </li>
            <li>
              <strong>Integration credentials</strong> — OAuth access and refresh
              tokens for each connected provider, stored encrypted at rest.
              These are never returned to your browser nor included in data
              exports.
            </li>
            <li>
              <strong>Billing data</strong> — handled by Stripe. We retain only
              a Stripe customer/subscription reference, your current plan, and
              renewal dates. Card details never touch our systems.
            </li>
            <li>
              <strong>Technical data</strong> — IP address, user-agent, request
              IDs, and error traces. Retained for security and debugging.
            </li>
          </ul>
        </Section>

        <Section title="3. How we use it">
          <ul className="list-disc space-y-2 pl-5">
            <li>
              To run the assistant — your documents are chunked, embedded, and
              retrieved on-demand to ground AI outputs in your workspace&apos;s
              knowledge.
            </li>
            <li>
              To operate background agents (meeting notes, policy propagation,
              version diffs, support draft generation, onboarding setup).
            </li>
            <li>
              To enforce plan limits, rate limits, and security policies.
            </li>
            <li>
              To send transactional email (invites, password resets, payment
              receipts).
            </li>
            <li>
              To diagnose errors and detect abuse via Sentry, Langfuse, and
              our own structured logs.
            </li>
          </ul>
          <p className="mt-3">
            We do <strong>not</strong> use your workspace content to train any
            general-purpose AI model. Embeddings are derived per-workspace and
            never mixed across tenants.
          </p>
        </Section>

        <Section title="4. Sub-processors">
          <p>
            We use the following third-party processors to deliver the service.
            Each is contractually bound to confidentiality and security
            obligations consistent with this policy:
          </p>
          <div className="overflow-x-auto">
            <table className="mt-3 w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-border text-left">
                  <th className="py-2 pr-4 font-medium">Processor</th>
                  <th className="py-2 pr-4 font-medium">Purpose</th>
                </tr>
              </thead>
              <tbody className="text-muted-foreground">
                {SUBPROCESSORS.map((row) => (
                  <tr key={row.name} className="border-b border-border/40">
                    <td className="py-2 pr-4 text-foreground">{row.name}</td>
                    <td className="py-2 pr-4">{row.purpose}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        <Section title="5. Retention">
          <p>
            We retain workspace content for as long as your subscription is
            active. Deletion of your account (Settings → Danger zone) removes
            all your conversations and personal data immediately; if you are
            the sole admin, the entire workspace is removed. Backups roll over
            on a 30-day window.
          </p>
        </Section>

        <Section title="6. Your rights">
          <p>
            Depending on your jurisdiction (GDPR, CCPA, India&apos;s DPDP Act,
            others), you have rights to:
          </p>
          <ul className="list-disc space-y-1 pl-5">
            <li>Access your data — use Settings → Your data → Download my data.</li>
            <li>Correct inaccurate data — edit it in Settings or contact us.</li>
            <li>
              Delete your account — Settings → Danger zone, or email{" "}
              <a className="underline" href="mailto:privacy@nirnayaiq.com">
                privacy@nirnayaiq.com
              </a>
              .
            </li>
            <li>
              Object to or restrict processing in specific cases — contact us.
            </li>
            <li>
              Withdraw consent for non-essential cookies via the consent banner.
            </li>
          </ul>
        </Section>

        <Section title="7. Cookies">
          <p>
            We use a small number of essential cookies to keep you signed in
            and to remember your theme preference. We do not currently set
            third-party advertising cookies. If we introduce analytics
            tracking, we will gate it behind the consent banner.
          </p>
        </Section>

        <Section title="8. Security">
          <p>
            All traffic is HTTPS. Database rows are isolated per workspace via
            row-level security policies; access tokens are encrypted at rest.
            We follow the principle of least privilege for internal access.
            Report a vulnerability at{" "}
            <a className="underline" href="mailto:security@nirnayaiq.com">
              security@nirnayaiq.com
            </a>
            .
          </p>
        </Section>

        <Section title="9. Changes to this policy">
          <p>
            Material changes will be announced by email to workspace admins
            and via an in-app notification at least 14 days before they take
            effect.
          </p>
        </Section>
      </div>
    </article>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold tracking-tight text-foreground">
        {title}
      </h2>
      <div className="space-y-3 text-sm text-muted-foreground">{children}</div>
    </section>
  );
}

function PlaceholderNotice() {
  return (
    <aside className="mb-10 rounded-md border border-amber-500/40 bg-amber-50/60 p-4 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-950/30 dark:text-amber-200">
      <p className="font-medium">Pre-launch placeholder content</p>
      <p className="mt-1">
        This document is a structural draft. The narrative reflects current
        product behaviour but has not been reviewed by counsel. Replace
        before commercial launch.
      </p>
    </aside>
  );
}

const SUBPROCESSORS: { name: string; purpose: string }[] = [
  { name: "Supabase", purpose: "Hosted Postgres, Auth, Storage" },
  { name: "Vercel", purpose: "Frontend hosting and edge runtime" },
  { name: "Railway", purpose: "Backend (FastAPI) hosting" },
  { name: "Google (Gemini, OAuth)", purpose: "LLM, embeddings, Drive/Gmail OAuth" },
  { name: "Anthropic", purpose: "Optional LLM provider (Claude)" },
  { name: "OpenAI", purpose: "Optional embeddings provider" },
  { name: "Stripe", purpose: "Billing and subscription management" },
  { name: "Resend", purpose: "Transactional email and inbound forwarding" },
  { name: "Upstash", purpose: "Rate limiting and ephemeral cache (Redis)" },
  { name: "Inngest", purpose: "Background job orchestration" },
  { name: "Sentry", purpose: "Error monitoring" },
  { name: "Langfuse", purpose: "LLM tracing and observability" },
  { name: "Notion / Slack / Microsoft / Atlassian / GitHub / Dropbox", purpose: "Integration providers, per workspace opt-in" },
];
