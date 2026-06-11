"use client";

/**
 * Global document-status toast — V4 #60.
 *
 * Mounted once in the dashboard layout so the user sees a "welcome back"
 * toast when a long-processing upload finishes, regardless of which page
 * they're on. If the tab was hidden when the row flipped, we defer the
 * toast until `visibilitychange` flips back to visible (so it lands at
 * the moment the user is actually looking).
 *
 * The documents page passes `silentToasts: true` to its own realtime hook
 * to prevent double-firing.
 */
import { useDocumentsRealtime } from "./use-documents-realtime";

const noop = () => {};

export function useDocumentReadyToast() {
  useDocumentsRealtime({
    onUpsert: noop,
    onRemove: noop,
  });
}
