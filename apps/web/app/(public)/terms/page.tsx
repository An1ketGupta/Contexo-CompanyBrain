import type { Metadata } from "next";

// IMPORTANT — LEGAL_PLACEHOLDER
// ─────────────────────────────────────────────────────────────────────────
// Structural placeholder. The clauses below reflect how the product
// actually behaves (plans, quotas, account deletion, acceptable use) but
// are NOT lawyer-reviewed. Replace narrative before commercial launch.
// ─────────────────────────────────────────────────────────────────────────

export const metadata: Metadata = {
  title: "Terms of Service",
  description: "Terms governing your use of the Contexo platform.",
};

const EFFECTIVE_DATE = "Pending — set once final copy is reviewed.";

export default function TermsPage() {
  return (
    <article className="mx-auto max-w-3xl px-4 py-12 sm:py-16">
      <header className="mb-8 border-b border-border pb-6">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">
          Legal
        </p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight text-foreground">
          Terms of Service
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Effective date: {EFFECTIVE_DATE}
        </p>
      </header>

      <PlaceholderNotice />

      <div className="space-y-10 leading-relaxed text-foreground">
        <Section title="1. Agreement">
          <p>
            These Terms of Service govern your use of the Contexo platform.
            By creating an account or accessing a workspace, you agree to be
            bound by them. If you are using the service on behalf of an
            organisation, you confirm you have authority to bind that
            organisation.
          </p>
        </Section>

        <Section title="2. The service">
          <p>
            Contexo is a multi-tenant SaaS platform that ingests workspace
            content, indexes it for retrieval, and provides AI-assisted
            outputs grounded in that content. The service includes a web
            application, a FastAPI backend, background agents, a Chrome
            extension, and integrations with third-party providers (Google
            Drive, Notion, Slack, Gmail, Microsoft Graph, Confluence, GitHub,
            Dropbox, email forwarding).
          </p>
        </Section>

        <Section title="3. Plans and billing">
          <ul className="list-disc space-y-2 pl-5">
            <li>
              Plans (Free, Starter, Team, Business) carry monthly query,
              document, and seat quotas listed on the Pricing page and
              enforced in-app.
            </li>
            <li>
              Subscriptions are billed by Stripe. Charges renew automatically
              until cancelled via Settings → Billing.
            </li>
            <li>
              Refunds are at our discretion and are not granted for usage
              already consumed.
            </li>
            <li>
              Failure to pay an invoice may downgrade your workspace to the
              Free tier; access to historical data is preserved.
            </li>
          </ul>
        </Section>

        <Section title="4. Acceptable use">
          <p>You agree not to:</p>
          <ul className="list-disc space-y-2 pl-5">
            <li>
              Upload content you do not have the right to use, including
              third-party copyrighted material without permission.
            </li>
            <li>
              Use the service to generate or distribute unlawful, defamatory,
              harassing, or harmful content.
            </li>
            <li>
              Attempt to reverse engineer the service, bypass rate limits,
              extract embeddings in bulk, or probe for security
              vulnerabilities outside the responsible-disclosure process.
            </li>
            <li>
              Resell the service or expose it as a generic third-party API.
            </li>
          </ul>
        </Section>

        <Section title="5. Content and ownership">
          <p>
            You retain ownership of all content you upload. You grant us a
            limited licence to host, process, and surface that content
            internally to your workspace, and to operate background agents
            on it. We do not use your content to train any general-purpose AI
            model. AI outputs generated for you are yours to use, subject to
            third-party rights in any source material.
          </p>
        </Section>

        <Section title="6. AI outputs">
          <p>
            AI-generated text is probabilistic and may be incorrect. You are
            responsible for reviewing outputs before publishing, sending, or
            relying on them. We surface the source documents used for each
            output to help you verify accuracy.
          </p>
        </Section>

        <Section title="7. Termination">
          <p>
            You may delete your account at any time via Settings → Danger
            zone. If you are the sole admin of a workspace, deletion removes
            the entire workspace. We may suspend or terminate access for
            breach of these terms, for non-payment, or to comply with legal
            obligations.
          </p>
        </Section>

        <Section title="8. Liability">
          <p>
            To the maximum extent permitted by law, our aggregate liability
            for any claim arising out of or related to the service is limited
            to the fees you paid in the twelve months preceding the claim. We
            are not liable for indirect, incidental, or consequential damages.
            The service is provided &ldquo;as is&rdquo; without warranties of
            any kind.
          </p>
        </Section>

        <Section title="9. Governing law">
          <p>
            These terms are governed by the laws of the jurisdiction in which
            Contexo is registered (to be confirmed in the final version).
            Disputes will be resolved in the courts of that jurisdiction.
          </p>
        </Section>

        <Section title="10. Changes">
          <p>
            We may update these terms from time to time. Material changes
            will be announced by email to workspace admins and via an in-app
            notification at least 14 days before they take effect. Continued
            use after the effective date constitutes acceptance.
          </p>
        </Section>

        <Section title="11. Contact">
          <p>
            Questions about these terms?{" "}
            <a className="underline" href="mailto:legal@nirnayaiq.com">
              legal@nirnayaiq.com
            </a>
            .
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
        This document is a structural draft. The clauses reflect current
        product behaviour but have not been reviewed by counsel. Replace
        before commercial launch.
      </p>
    </aside>
  );
}
