"use client";

/**
 * Crisp.chat — production support widget.
 *
 * Chosen over Intercom because Crisp's free tier (2 seats, unlimited
 * conversations) is genuinely free and adequate for pre-seed. Same JS API
 * shape, easy to swap later. Disabled when NEXT_PUBLIC_CRISP_WEBSITE_ID is
 * unset — the widget never loads and zero network calls happen.
 *
 * Identity: we tag every session with the logged-in user + org so the
 * inbound chat lands with full context (no "hi can you tell me your email").
 * The crisp `$crisp.push` API queues calls before the JS finishes loading
 * so we can call it synchronously without race-conditions.
 */
import { useEffect } from "react";

import { useCurrentUser } from "@/hooks/use-user";

declare global {
  interface Window {
    $crisp?: unknown[];
    CRISP_WEBSITE_ID?: string;
    CRISP_TOKEN_ID?: string;
  }
}

const CRISP_SCRIPT_ID = "crisp-widget-script";

export function CrispProvider() {
  const { user, organization } = useCurrentUser();
  const websiteId = process.env.NEXT_PUBLIC_CRISP_WEBSITE_ID;

  useEffect(() => {
    if (!websiteId) return;
    if (typeof window === "undefined") return;

    // Init the queue + global id. Crisp reads CRISP_WEBSITE_ID synchronously
    // when the script first executes.
    window.$crisp = window.$crisp || [];
    window.CRISP_WEBSITE_ID = websiteId;

    if (!document.getElementById(CRISP_SCRIPT_ID)) {
      const script = document.createElement("script");
      script.id = CRISP_SCRIPT_ID;
      script.src = "https://client.crisp.chat/l.js";
      script.async = true;
      document.head.appendChild(script);
    }
  }, [websiteId]);

  // Identify — pushes are queued safely until the JS loads.
  useEffect(() => {
    if (!websiteId || typeof window === "undefined") return;
    const $crisp = window.$crisp;
    if (!$crisp) return;

    if (user?.email) {
      $crisp.push(["set", "user:email", [user.email]]);
    }
    if (user?.display_name) {
      $crisp.push(["set", "user:nickname", [user.display_name]]);
    }
    if (organization?.name) {
      $crisp.push(["set", "user:company", [organization.name]]);
    }
    if (organization || user) {
      $crisp.push([
        "set",
        "session:data",
        [
          [
            ["user_id", user?.id ?? ""],
            ["org_id", user?.org_id ?? ""],
            ["org_plan", organization?.plan ?? "unknown"],
            ["role", user?.role ?? "member"],
          ],
        ],
      ]);
    }
  }, [
    websiteId,
    user?.id,
    user?.email,
    user?.display_name,
    user?.role,
    user?.org_id,
    organization?.name,
    organization?.plan,
  ]);

  return null;
}

/**
 * Programmatic helpers for "Contact Support" links elsewhere in the app.
 * No-op when Crisp isn't loaded (env var unset or script blocked).
 */
export function openCrispChat(initialMessage?: string): boolean {
  if (typeof window === "undefined") return false;
  const $crisp = window.$crisp;
  if (!$crisp) return false;
  $crisp.push(["do", "chat:open"]);
  if (initialMessage) {
    $crisp.push(["do", "message:send", ["text", initialMessage]]);
  }
  return true;
}

export function isCrispAvailable(): boolean {
  if (typeof window === "undefined") return false;
  return !!process.env.NEXT_PUBLIC_CRISP_WEBSITE_ID;
}
