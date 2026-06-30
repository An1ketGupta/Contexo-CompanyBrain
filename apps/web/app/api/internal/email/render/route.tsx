import { NextRequest, NextResponse } from "next/server";
import crypto from "node:crypto";
import React from "react";
import { render } from "@react-email/render";
import {
  InviteEmail,
  inviteSubject,
  type InviteEmailProps,
} from "@/emails/invite";
import {
  WelcomeEmail,
  welcomeSubject,
  type WelcomeEmailProps,
} from "@/emails/welcome";
import {
  DocumentReadyEmail,
  documentReadySubject,
  type DocumentReadyEmailProps,
} from "@/emails/document-ready";
import {
  QuotaWarningEmail,
  quotaWarningSubject,
  type QuotaWarningEmailProps,
} from "@/emails/quota-warning";
import {
  QuotaExceededEmail,
  quotaExceededSubject,
  type QuotaExceededEmailProps,
} from "@/emails/quota-exceeded";
import {
  WeeklyDigestEmail,
  weeklyDigestSubject,
  type WeeklyDigestEmailProps,
} from "@/emails/weekly-digest";
import {
  KnowledgeRefreshEmail,
  knowledgeRefreshSubject,
  type KnowledgeRefreshEmailProps,
} from "@/emails/knowledge-refresh";
import {
  KnowledgeGapAlertEmail,
  knowledgeGapAlertSubject,
  type KnowledgeGapAlertEmailProps,
} from "@/emails/knowledge-gap-alert";
import {
  ApprovalRequestEmail,
  approvalRequestSubject,
  type ApprovalRequestEmailProps,
} from "@/emails/approval-request";
import {
  ApprovalResolvedEmail,
  approvalResolvedSubject,
  type ApprovalResolvedEmailProps,
} from "@/emails/approval-resolved";
import {
  ApprovalReminderEmail,
  approvalReminderSubject,
  type ApprovalReminderEmailProps,
} from "@/emails/approval-reminder";
import {
  OnboardingWelcomeEmail,
  onboardingWelcomeSubject,
  type OnboardingWelcomeEmailProps,
} from "@/emails/onboarding-welcome";
import {
  AcknowledgementReminderEmail,
  acknowledgementReminderSubject,
  type AcknowledgementReminderEmailProps,
} from "@/emails/acknowledgement-reminder";
import {
  FeedbackThresholdAlertEmail,
  feedbackThresholdAlertSubject,
  type FeedbackThresholdAlertEmailProps,
} from "@/emails/feedback-threshold-alert";
import {
  ScheduledUsageSummaryEmail,
  scheduledUsageSummarySubject,
  type ScheduledUsageSummaryEmailProps,
} from "@/emails/scheduled-usage-summary";
import {
  ScheduledKnowledgeHealthEmail,
  scheduledKnowledgeHealthSubject,
  type ScheduledKnowledgeHealthEmailProps,
} from "@/emails/scheduled-knowledge-health";
import {
  InternalAnnouncementEmail,
  internalAnnouncementSubject,
  type InternalAnnouncementEmailProps,
} from "@/emails/internal-announcement";
import {
  WeeklyBriefingEmail,
  weeklyBriefingSubject,
  type WeeklyBriefingEmailProps,
} from "@/emails/weekly-briefing";
import {
  RecruitingPublishedEmail,
  recruitingPublishedSubject,
  type RecruitingPublishedEmailProps,
} from "@/emails/recruiting-published";
import {
  OnboardingLoiReadyEmail,
  onboardingLoiReadySubject,
  type OnboardingLoiReadyEmailProps,
} from "@/emails/onboarding-loi-ready";
import {
  OnboardingLoiToCandidateEmail,
  onboardingLoiToCandidateSubject,
  type OnboardingLoiToCandidateEmailProps,
} from "@/emails/onboarding-loi-to-candidate";
import {
  OnboardingBgvRequestEmail,
  onboardingBgvRequestSubject,
  type OnboardingBgvRequestEmailProps,
} from "@/emails/onboarding-bgv-request";
import {
  OnboardingBgvReminderEmail,
  onboardingBgvReminderSubject,
  type OnboardingBgvReminderEmailProps,
} from "@/emails/onboarding-bgv-reminder";
import {
  OnboardingCandidateRefsReminderEmail,
  onboardingCandidateRefsReminderSubject,
  type OnboardingCandidateRefsReminderEmailProps,
} from "@/emails/onboarding-candidate-refs-reminder";
import {
  OnboardingOfferBundleReadyEmail,
  onboardingOfferBundleReadySubject,
  type OnboardingOfferBundleReadyEmailProps,
} from "@/emails/onboarding-offer-bundle-ready";
import {
  OnboardingOfferToCandidateEmail,
  onboardingOfferToCandidateSubject,
  type OnboardingOfferToCandidateEmailProps,
} from "@/emails/onboarding-offer-to-candidate";
import {
  OnboardingPoliciesPendingEmail,
  onboardingPoliciesPendingSubject,
  type OnboardingPoliciesPendingEmailProps,
} from "@/emails/onboarding-policies-pending";
import {
  OnboardingInductionReadyEmail,
  onboardingInductionReadySubject,
  type OnboardingInductionReadyEmailProps,
} from "@/emails/onboarding-induction-ready";
import {
  OnboardingEsignStalledEmail,
  onboardingEsignStalledSubject,
  type OnboardingEsignStalledEmailProps,
} from "@/emails/onboarding-esign-stalled";

/**
 * Server-to-server email rendering. The FastAPI Inngest worker calls this
 * route to turn { template, data } into { html, subject }, then ships the
 * result to Resend.
 *
 * Auth: x-internal-signature is hex(hmac-sha256(secret, rawBody)). The
 * shared secret is INTERNAL_EMAIL_SECRET on both services. Constant-time
 * compare so a timing oracle can't leak the signature.
 *
 * This route should NEVER be exposed beyond the internal network in
 * production. In dev, Inngest hits localhost; in prod, both services live
 * inside the same Railway/Vercel mesh and we additionally rely on the
 * shared secret being unguessable.
 */
export const runtime = "nodejs"; // crypto + buffer parity with our worker

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Template<P> = {
  Component: React.ComponentType<P>;
  subject: (props: P) => string;
};

const TEMPLATES = {
  invite: { Component: InviteEmail, subject: inviteSubject } satisfies Template<InviteEmailProps>,
  welcome: { Component: WelcomeEmail, subject: welcomeSubject } satisfies Template<WelcomeEmailProps>,
  document_ready: {
    Component: DocumentReadyEmail,
    subject: documentReadySubject,
  } satisfies Template<DocumentReadyEmailProps>,
  quota_warning: {
    Component: QuotaWarningEmail,
    subject: quotaWarningSubject,
  } satisfies Template<QuotaWarningEmailProps>,
  quota_exceeded: {
    Component: QuotaExceededEmail,
    subject: quotaExceededSubject,
  } satisfies Template<QuotaExceededEmailProps>,
  weekly_digest: {
    Component: WeeklyDigestEmail,
    subject: weeklyDigestSubject,
  } satisfies Template<WeeklyDigestEmailProps>,
  knowledge_refresh: {
    Component: KnowledgeRefreshEmail,
    subject: knowledgeRefreshSubject,
  } satisfies Template<KnowledgeRefreshEmailProps>,
  knowledge_gap_alert: {
    Component: KnowledgeGapAlertEmail,
    subject: knowledgeGapAlertSubject,
  } satisfies Template<KnowledgeGapAlertEmailProps>,
  approval_request: {
    Component: ApprovalRequestEmail,
    subject: approvalRequestSubject,
  } satisfies Template<ApprovalRequestEmailProps>,
  approval_resolved: {
    Component: ApprovalResolvedEmail,
    subject: approvalResolvedSubject,
  } satisfies Template<ApprovalResolvedEmailProps>,
  approval_reminder: {
    Component: ApprovalReminderEmail,
    subject: approvalReminderSubject,
  } satisfies Template<ApprovalReminderEmailProps>,
  onboarding_welcome: {
    Component: OnboardingWelcomeEmail,
    subject: onboardingWelcomeSubject,
  } satisfies Template<OnboardingWelcomeEmailProps>,
  acknowledgement_reminder: {
    Component: AcknowledgementReminderEmail,
    subject: acknowledgementReminderSubject,
  } satisfies Template<AcknowledgementReminderEmailProps>,
  feedback_threshold_alert: {
    Component: FeedbackThresholdAlertEmail,
    subject: feedbackThresholdAlertSubject,
  } satisfies Template<FeedbackThresholdAlertEmailProps>,
  scheduled_usage_summary: {
    Component: ScheduledUsageSummaryEmail,
    subject: scheduledUsageSummarySubject,
  } satisfies Template<ScheduledUsageSummaryEmailProps>,
  scheduled_knowledge_health: {
    Component: ScheduledKnowledgeHealthEmail,
    subject: scheduledKnowledgeHealthSubject,
  } satisfies Template<ScheduledKnowledgeHealthEmailProps>,
  internal_announcement: {
    Component: InternalAnnouncementEmail,
    subject: internalAnnouncementSubject,
  } satisfies Template<InternalAnnouncementEmailProps>,
  weekly_briefing: {
    Component: WeeklyBriefingEmail,
    subject: weeklyBriefingSubject,
  } satisfies Template<WeeklyBriefingEmailProps>,
  recruiting_published: {
    Component: RecruitingPublishedEmail,
    subject: recruitingPublishedSubject,
  } satisfies Template<RecruitingPublishedEmailProps>,
  onboarding_loi_ready: {
    Component: OnboardingLoiReadyEmail,
    subject: onboardingLoiReadySubject,
  } satisfies Template<OnboardingLoiReadyEmailProps>,
  onboarding_loi_to_candidate: {
    Component: OnboardingLoiToCandidateEmail,
    subject: onboardingLoiToCandidateSubject,
  } satisfies Template<OnboardingLoiToCandidateEmailProps>,
  onboarding_bgv_request: {
    Component: OnboardingBgvRequestEmail,
    subject: onboardingBgvRequestSubject,
  } satisfies Template<OnboardingBgvRequestEmailProps>,
  onboarding_bgv_reminder: {
    Component: OnboardingBgvReminderEmail,
    subject: onboardingBgvReminderSubject,
  } satisfies Template<OnboardingBgvReminderEmailProps>,
  onboarding_candidate_refs_reminder: {
    Component: OnboardingCandidateRefsReminderEmail,
    subject: onboardingCandidateRefsReminderSubject,
  } satisfies Template<OnboardingCandidateRefsReminderEmailProps>,
  onboarding_offer_bundle_ready: {
    Component: OnboardingOfferBundleReadyEmail,
    subject: onboardingOfferBundleReadySubject,
  } satisfies Template<OnboardingOfferBundleReadyEmailProps>,
  onboarding_offer_to_candidate: {
    Component: OnboardingOfferToCandidateEmail,
    subject: onboardingOfferToCandidateSubject,
  } satisfies Template<OnboardingOfferToCandidateEmailProps>,
  onboarding_policies_pending: {
    Component: OnboardingPoliciesPendingEmail,
    subject: onboardingPoliciesPendingSubject,
  } satisfies Template<OnboardingPoliciesPendingEmailProps>,
  onboarding_induction_ready: {
    Component: OnboardingInductionReadyEmail,
    subject: onboardingInductionReadySubject,
  } satisfies Template<OnboardingInductionReadyEmailProps>,
  onboarding_esign_stalled: {
    Component: OnboardingEsignStalledEmail,
    subject: onboardingEsignStalledSubject,
  } satisfies Template<OnboardingEsignStalledEmailProps>,
};

type TemplateName = keyof typeof TEMPLATES;

function isTemplateName(value: unknown): value is TemplateName {
  return typeof value === "string" && Object.hasOwn(TEMPLATES, value);
}

export async function POST(req: NextRequest): Promise<Response> {
  const secret = process.env.INTERNAL_EMAIL_SECRET;
  if (!secret) {
    return NextResponse.json(
      { code: "misconfigured", message: "INTERNAL_EMAIL_SECRET not set." },
      { status: 503 },
    );
  }

  const signature = req.headers.get("x-internal-signature");
  if (!signature) {
    return NextResponse.json(
      { code: "unauthorized", message: "Missing signature." },
      { status: 401 },
    );
  }

  const raw = await req.text();
  const expected = crypto
    .createHmac("sha256", secret)
    .update(raw)
    .digest("hex");

  const sigBuf = Buffer.from(signature, "hex");
  const expBuf = Buffer.from(expected, "hex");
  if (
    sigBuf.length !== expBuf.length ||
    !crypto.timingSafeEqual(sigBuf, expBuf)
  ) {
    return NextResponse.json(
      { code: "unauthorized", message: "Bad signature." },
      { status: 401 },
    );
  }

  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return NextResponse.json(
      { code: "bad_request", message: "Body must be JSON." },
      { status: 400 },
    );
  }

  if (
    !body ||
    typeof body !== "object" ||
    !("template" in body) ||
    !("data" in body)
  ) {
    return NextResponse.json(
      { code: "bad_request", message: "Expected { template, data }." },
      { status: 400 },
    );
  }

  const { template, data } = body as { template: unknown; data: unknown };
  if (!isTemplateName(template)) {
    return NextResponse.json(
      {
        code: "bad_request",
        message: `Unknown template '${String(template)}'.`,
      },
      { status: 400 },
    );
  }

  try {
    const def = TEMPLATES[template];
    // We trust the HMAC-verified FastAPI caller to send well-shaped data;
    // a runtime type mismatch surfaces as a React render error → 500 here.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const Component = def.Component as React.ComponentType<any>;
    const html = await render(<Component {...(data as object)} />, {
      pretty: false,
    });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const subject = (def.subject as (p: any) => string)(data);
    return NextResponse.json({ html, subject });
  } catch (err) {
    console.error("[email/render] failed", { template, err });
    return NextResponse.json(
      {
        code: "render_failed",
        message: err instanceof Error ? err.message : "Render error",
      },
      { status: 500 },
    );
  }
}
