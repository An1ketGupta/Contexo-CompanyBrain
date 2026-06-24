"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Check, ChevronLeft, ExternalLink, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { networkError, parseApiError, reportApiError } from "@/lib/errors";
import { useUsage } from "@/hooks/use-usage";

interface BillingStatus {
  plan: string;
  plan_status: string;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  has_billing_account: boolean;
  is_admin: boolean;
}

interface TierView {
  plan: "starter" | "team" | "business";
  interval: "month" | "year";
  stripe_price_id: string;
  unit_amount_cents: number;
  currency: string;
  quota_documents: number | null;
  quota_queries_monthly: number | null;
  quota_seats: number | null;
}

interface PlansResponse {
  plans: TierView[];
  mode: string;
}

const PLAN_COPY: Record<
  TierView["plan"],
  { label: string; pitch: string; bullets: string[] }
> = {
  starter: {
    label: "Starter",
    pitch: "For small teams getting started with their AI work platform.",
    bullets: [
      "Hybrid retrieval over your full knowledge base",
      "All 9 integrations",
      "Email support",
    ],
  },
  team: {
    label: "Team",
    pitch: "For growing teams that need more headroom and approvals.",
    bullets: [
      "Approval workflows + audit log",
      "Higher monthly query budget",
      "Priority support",
    ],
  },
  business: {
    label: "Business",
    pitch: "Unlimited usage, dedicated onboarding, and SSO (on request).",
    bullets: [
      "Unlimited queries and documents",
      "Unlimited seats",
      "Dedicated onboarding + SLA",
    ],
  },
};

const ACTIVE_STATUSES = ["active", "trialing", "past_due"] as const;

const STATUS_COPY: Record<string, { label: string; tone: string }> = {
  active: { label: "Active", tone: "text-emerald-600 dark:text-emerald-400" },
  trialing: { label: "Trial", tone: "text-sky-600 dark:text-sky-400" },
  past_due: { label: "Past due", tone: "text-amber-600 dark:text-amber-400" },
  unpaid: { label: "Unpaid", tone: "text-destructive" },
  canceled: { label: "Canceled", tone: "text-muted-foreground" },
  incomplete: { label: "Incomplete", tone: "text-amber-600 dark:text-amber-400" },
  incomplete_expired: { label: "Expired", tone: "text-destructive" },
  paused: { label: "Paused", tone: "text-muted-foreground" },
  inactive: { label: "No subscription", tone: "text-muted-foreground" },
};

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as T;
};

function formatCurrency(amount_cents: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency.toUpperCase(),
      minimumFractionDigits: amount_cents % 100 === 0 ? 0 : 2,
    }).format(amount_cents / 100);
  } catch {
    return `$${(amount_cents / 100).toFixed(2)}`;
  }
}

function formatQuota(value: number | null, unit: string): string {
  if (value === null) return `Unlimited ${unit}`;
  return `${value.toLocaleString()} ${unit}`;
}

function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export default function BillingPage() {
  return (
    <Suspense fallback={<BillingSkeleton />}>
      <BillingPageInner />
    </Suspense>
  );
}

function BillingPageInner() {
  const searchParams = useSearchParams();
  const [interval, setInterval] = useState<"month" | "year">("month");
  const [upgrading, setUpgrading] = useState<string | null>(null);
  const [portalLoading, setPortalLoading] = useState(false);
  // Day 12: webhook race-condition guard. Stripe redirects the user to
  // ?checkout=success the moment Checkout completes, but the webhook
  // that flips `organizations.plan` lands on our backend a beat later.
  // Without an explicit "Processing…" state, the user briefly sees their
  // OLD plan after paying for the new one.
  const [processingCheckout, setProcessingCheckout] = useState(false);
  // Track the most recent plan we've observed so the poller can decide
  // when Stripe's webhook has actually landed. Using a ref avoids stale
  // closures inside the polling timeout chain.
  const pollAbortRef = useRef<{ cancelled: boolean }>({ cancelled: false });

  const {
    data: status,
    error: statusError,
    isLoading: statusLoading,
    mutate: refetchStatus,
  } = useSWR<BillingStatus>("/api/billing/status", fetcher, {
    revalidateOnFocus: true,
  });

  const { data: plansResp, isLoading: plansLoading } = useSWR<PlansResponse>(
    "/api/billing/plans",
    fetcher,
  );

  const { refresh: refetchUsage } = useUsage();

  // Handle ?checkout=success / canceled. On success we poll the status
  // endpoint until either (a) the plan flips away from the pre-checkout
  // snapshot or (b) we exhaust our attempt budget — whichever comes
  // first. Six attempts at 1.5s ≈ 9s, which covers >99% of webhook
  // delivery times we've measured in test mode.
  useEffect(() => {
    const checkout = searchParams.get("checkout");
    if (!checkout) return;

    if (checkout === "canceled") {
      toast.info("Checkout canceled. No changes were made.");
    } else if (checkout === "success") {
      const initialPlan = status?.plan ?? "free";
      const initialPlanStatus = status?.plan_status ?? "inactive";

      setProcessingCheckout(true);
      pollAbortRef.current = { cancelled: false };
      const abortHandle = pollAbortRef.current;
      const toastId = toast.loading("Confirming your subscription with Stripe…");

      const poll = async (attempt: number) => {
        if (abortHandle.cancelled) return;
        const fresh = await refetchStatus();
        // Webhook landed: plan rolled forward OR plan_status flipped to
        // an active-equivalent state from the pre-checkout snapshot.
        const planChanged = fresh && fresh.plan !== initialPlan;
        const statusActivated =
          fresh &&
          fresh.plan_status !== initialPlanStatus &&
          (
            ACTIVE_STATUSES as readonly string[]
          ).includes(fresh.plan_status);

        if (planChanged || statusActivated) {
          if (abortHandle.cancelled) return;
          setProcessingCheckout(false);
          toast.success("Subscription active — your plan is updated.", { id: toastId });
          refetchUsage();
          return;
        }
        if (attempt >= 6) {
          if (abortHandle.cancelled) return;
          setProcessingCheckout(false);
          toast.message(
            "Stripe is still confirming your payment. Refresh this page in a moment to see the new plan.",
            { id: toastId, duration: 8000 },
          );
          return;
        }
        window.setTimeout(() => poll(attempt + 1), 1500);
      };

      // Kick off the first refetch immediately so a fast webhook (which
      // does happen for low-latency Stripe events) shows up in <1s.
      poll(1);
    }

    const url = new URL(window.location.href);
    url.searchParams.delete("checkout");
    url.searchParams.delete("session_id");
    window.history.replaceState({}, "", url.toString());

    return () => {
      // Cancel any pending poll if the user navigates away mid-confirmation.
      pollAbortRef.current.cancelled = true;
    };
    // We deliberately omit `status` from the dep array — the effect should
    // fire once when the redirect lands, not every time the polled status
    // refreshes. Capturing the initial snapshot inside the effect handles
    // the "what was the plan before?" comparison.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, refetchStatus, refetchUsage]);

  async function startCheckout(plan: TierView["plan"]) {
    if (upgrading) return;
    setUpgrading(plan);
    try {
      const res = await fetch("/api/billing/checkout-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan, interval }),
      });
      if (!res.ok) {
        reportApiError(await parseApiError(res));
        return;
      }
      const data = (await res.json()) as { checkout_url: string };
      window.location.href = data.checkout_url;
    } catch (err) {
      reportApiError(networkError(err));
    } finally {
      setUpgrading(null);
    }
  }

  async function openPortal() {
    if (portalLoading) return;
    setPortalLoading(true);
    try {
      const res = await fetch("/api/billing/portal-session", {
        method: "POST",
      });
      if (!res.ok) {
        reportApiError(await parseApiError(res));
        return;
      }
      const data = (await res.json()) as { portal_url: string };
      window.location.href = data.portal_url;
    } catch (err) {
      reportApiError(networkError(err));
    } finally {
      setPortalLoading(false);
    }
  }

  if (statusLoading || plansLoading) {
    return <BillingSkeleton />;
  }

  if (statusError || !status) {
    return (
      <Shell>
        <p className="text-sm text-destructive">
          Couldn&apos;t load your billing status. Refresh the page or try
          again in a moment.
        </p>
      </Shell>
    );
  }

  const plans = plansResp?.plans ?? [];
  const planByInterval = plans.filter((p) => p.interval === interval);
  const hasYearly = plans.some((p) => p.interval === "year");
  const statusInfo =
    STATUS_COPY[status.plan_status] ?? STATUS_COPY.inactive;
  const periodEnd = formatDate(status.current_period_end);

  return (
    <Shell>
      <div className="space-y-1">
        <Link
          href="/settings"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft className="h-3 w-3" />
          Settings
        </Link>
        <h1 className="text-xl font-semibold text-foreground">Billing</h1>
        <p className="text-sm text-muted-foreground">
          Manage your subscription, usage, and invoices.
        </p>
      </div>

      {processingCheckout && (
        <div className="flex items-center gap-3 rounded-md border border-primary/30 bg-primary/5 px-4 py-3 text-sm text-foreground">
          <Loader2 className="h-4 w-4 animate-spin text-primary" />
          <div className="min-w-0">
            <p className="font-medium">Confirming your subscription…</p>
            <p className="text-xs text-muted-foreground">
              Stripe usually finishes in a couple of seconds. We&apos;ll
              update your plan as soon as the confirmation lands.
            </p>
          </div>
        </div>
      )}

      <section className="rounded-lg border border-border bg-background p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">
              Current plan
            </p>
            <p className="mt-1 text-2xl font-semibold capitalize text-foreground">
              {status.plan === "free" ? "Free trial" : status.plan}
            </p>
            <p className={cn("mt-1 text-xs font-medium", statusInfo.tone)}>
              {statusInfo.label}
              {status.cancel_at_period_end && periodEnd
                ? ` · cancels ${periodEnd}`
                : !status.cancel_at_period_end && periodEnd
                  ? ` · renews ${periodEnd}`
                  : null}
            </p>
          </div>
          {status.has_billing_account && (
            <Button
              variant="outline"
              size="sm"
              onClick={openPortal}
              disabled={portalLoading || !status.is_admin}
              title={
                !status.is_admin
                  ? "Only workspace admins can manage billing."
                  : undefined
              }
            >
              {portalLoading && <Loader2 className="animate-spin" />}
              Manage billing
              <ExternalLink className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
        <UsageSummary />
      </section>

      <section className="space-y-3">
        <div className="flex items-end justify-between">
          <div>
            <h2 className="text-sm font-semibold text-foreground">
              Choose a plan
            </h2>
            <p className="text-xs text-muted-foreground">
              Upgrade, downgrade, or change billing cadence at any time.
            </p>
          </div>
          {hasYearly && (
            <div className="inline-flex rounded-full border border-border bg-muted p-0.5 text-xs">
              <IntervalPill
                active={interval === "month"}
                onClick={() => setInterval("month")}
              >
                Monthly
              </IntervalPill>
              <IntervalPill
                active={interval === "year"}
                onClick={() => setInterval("year")}
              >
                Yearly
              </IntervalPill>
            </div>
          )}
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          {(["starter", "team", "business"] as const).map((planKey) => {
            const tier = planByInterval.find((p) => p.plan === planKey);
            const copy = PLAN_COPY[planKey];
            const isCurrent =
              status.plan === planKey &&
              (ACTIVE_STATUSES as readonly string[]).includes(
                status.plan_status,
              );

            return (
              <article
                key={planKey}
                className={cn(
                  "flex flex-col rounded-lg border border-border bg-background p-5",
                  isCurrent && "border-primary/60 ring-1 ring-primary/30",
                )}
              >
                <div className="mb-3">
                  <p className="text-sm font-semibold text-foreground">
                    {copy.label}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {copy.pitch}
                  </p>
                </div>

                <div className="mb-3">
                  {tier ? (
                    <p className="text-2xl font-semibold tabular-nums text-foreground">
                      {formatCurrency(tier.unit_amount_cents, tier.currency)}
                      <span className="ml-1 text-xs font-normal text-muted-foreground">
                        /{tier.interval}
                      </span>
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      Contact sales for pricing
                    </p>
                  )}
                </div>

                <ul className="mb-4 space-y-1.5 text-xs text-muted-foreground">
                  {copy.bullets.map((b) => (
                    <li key={b} className="flex items-start gap-1.5">
                      <Check className="mt-0.5 h-3 w-3 shrink-0 text-primary" />
                      <span>{b}</span>
                    </li>
                  ))}
                  {tier && (
                    <>
                      <li className="flex items-start gap-1.5">
                        <Check className="mt-0.5 h-3 w-3 shrink-0 text-primary" />
                        <span>
                          {formatQuota(tier.quota_queries_monthly, "queries / month")}
                        </span>
                      </li>
                      <li className="flex items-start gap-1.5">
                        <Check className="mt-0.5 h-3 w-3 shrink-0 text-primary" />
                        <span>{formatQuota(tier.quota_documents, "documents")}</span>
                      </li>
                      <li className="flex items-start gap-1.5">
                        <Check className="mt-0.5 h-3 w-3 shrink-0 text-primary" />
                        <span>{formatQuota(tier.quota_seats, "seats")}</span>
                      </li>
                    </>
                  )}
                </ul>

                <div className="mt-auto">
                  {isCurrent ? (
                    <Button variant="secondary" className="w-full" disabled>
                      Current plan
                    </Button>
                  ) : (
                    <Button
                      className="w-full"
                      onClick={() => startCheckout(planKey)}
                      disabled={!tier || !status.is_admin || upgrading === planKey}
                      title={
                        !status.is_admin
                          ? "Only workspace admins can change billing."
                          : undefined
                      }
                    >
                      {upgrading === planKey && (
                        <Loader2 className="animate-spin" />
                      )}
                      {status.plan === "free"
                        ? "Subscribe"
                        : "Switch to " + copy.label}
                    </Button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </section>

      {!status.is_admin && (
        <p className="text-xs text-muted-foreground">
          Only workspace admins can change billing. Ask an admin to upgrade
          if you need more headroom.
        </p>
      )}
    </Shell>
  );
}

function IntervalPill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full px-3 py-1 transition",
        active
          ? "bg-background text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function UsageSummary() {
  const { usage } = useUsage();
  if (!usage) return null;

  if (usage.unlimited) {
    return (
      <div className="mt-4 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
        Unlimited monthly queries on the {usage.plan} plan.
      </div>
    );
  }

  const limit = Math.max(usage.limit ?? 1, 1);
  const pct = Math.min((usage.used / limit) * 100, 100);
  const resetDate = formatDate(usage.reset_at);

  return (
    <div className="mt-4 space-y-1.5">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          <span className="tabular-nums text-foreground">
            {usage.used.toLocaleString()}
          </span>
          {" / "}
          <span className="tabular-nums">{limit.toLocaleString()}</span>{" "}
          queries this month
        </span>
        {resetDate && <span>resets {resetDate}</span>}
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-500",
            usage.used >= limit
              ? "bg-destructive"
              : pct >= 80
                ? "bg-amber-500"
                : "bg-primary",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function BillingSkeleton() {
  return (
    <Shell>
      <div className="space-y-2">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-6 w-32" />
      </div>
      <Skeleton className="h-28 w-full" />
      <div className="grid gap-4 md:grid-cols-3">
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 md:p-8">{children}</div>
  );
}
