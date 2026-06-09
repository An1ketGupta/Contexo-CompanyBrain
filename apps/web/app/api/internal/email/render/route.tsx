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
