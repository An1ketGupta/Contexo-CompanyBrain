"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { useShortcutsPanel } from "@/components/ui/shortcuts-panel-context";

/** Selector for the sidebar conversation search input. The conversation
 *  sidebar's search box marks itself with this attribute so the `⌘/`
 *  shortcut can focus it without a ref hierarchy. */
export const CONVERSATION_SEARCH_SELECTOR = "[data-conversation-search]";

interface ShortcutOptions {
  /** Current assistant streaming state — required to know whether Esc should
   *  intercept (stop generation) or fall through to native handlers. */
  isStreaming: boolean;
  /** Called when the user hits Esc while a turn is streaming. */
  onStopGeneration: () => void;
  /** Called when ⌘⇧C is pressed. Receives no args — the hook can't know which
   *  message is "last", so pages must wire this themselves. */
  onCopyLastResponse: () => void;
}

/** Page-scoped keyboard shortcuts. ⌘K is handled globally by the command
 *  palette provider so it works on every page; the bindings here are wired
 *  per-page because they depend on chat state. */
export function useKeyboardShortcuts({
  isStreaming,
  onStopGeneration,
  onCopyLastResponse,
}: ShortcutOptions) {
  const router = useRouter();
  const { setOpen: setShortcutsOpen } = useShortcutsPanel();

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const isMac = typeof navigator !== "undefined" && /mac/i.test(navigator.platform);
      const meta = isMac ? e.metaKey : e.ctrlKey;

      const target = e.target as HTMLElement | null;
      const tag = target?.tagName ?? "";
      const isInputLike =
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        target?.isContentEditable === true;

      // Esc while streaming → stop generation. We don't preventDefault when not
      // streaming so Esc still closes dialogs, dropdowns, etc.
      if (e.key === "Escape" && isStreaming) {
        onStopGeneration();
        return;
      }

      // ⌘⇧C — copy last assistant response. Allow from inputs too so users
      // can paste mid-typing without losing focus.
      if (meta && e.shiftKey && e.key.toLowerCase() === "c") {
        e.preventDefault();
        onCopyLastResponse();
        return;
      }

      // ⌘/ — focus sidebar conversation search. Browsers don't use this combo.
      if (meta && e.key === "/") {
        e.preventDefault();
        const el = document.querySelector<HTMLInputElement>(CONVERSATION_SEARCH_SELECTOR);
        el?.focus();
        return;
      }

      // ⌘N — new conversation. Only outside inputs to avoid clobbering the
      // browser's native "new window" shortcut for power users; native Cmd+N
      // still wins because we don't preventDefault then.
      if (meta && e.key.toLowerCase() === "n" && !isInputLike) {
        e.preventDefault();
        router.push("/chat");
        return;
      }

      // `?` — show shortcuts panel. Skip while typing.
      if (e.key === "?" && !isInputLike && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        setShortcutsOpen(true);
        return;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isStreaming, onStopGeneration, onCopyLastResponse, router, setShortcutsOpen]);
}

/** Default copy-last-response handler used by chat pages. Looks up the most
 *  recent assistant message from a callback so we don't depend on a specific
 *  state shape, then writes to the clipboard with a toast. */
export async function copyToClipboardWithToast(text: string | null | undefined) {
  if (!text || !text.trim()) {
    toast.error("Nothing to copy yet.");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    toast.success("Last response copied.");
  } catch {
    // Insecure context (http://) — surfacing the failure is more useful than
    // silently dropping the user's request like CopyButton does, because the
    // shortcut has no visible affordance.
    toast.error("Couldn't access the clipboard. Try the Copy button instead.");
  }
}
