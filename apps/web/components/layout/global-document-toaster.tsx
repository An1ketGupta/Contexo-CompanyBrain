"use client";

import { useDocumentReadyToast } from "@/hooks/use-document-ready-toast";

/**
 * Mounts the document-ready toast hook once globally. Rendered from the
 * dashboard layout (server component) so the subscription lives for the
 * whole authenticated session, not just one page.
 */
export function GlobalDocumentToaster() {
  useDocumentReadyToast();
  return null;
}
