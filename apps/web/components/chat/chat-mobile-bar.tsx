"use client";

import { type ReactNode } from "react";
import { MobileConversationDrawer } from "./mobile-conversation-drawer";

interface ChatMobileBarProps {
  activeId: string | null;
  /** Slot for additional buttons (export, share-all, etc.) on the right. */
  trailing?: ReactNode;
}

/**
 * V3 Day 3 #27 — slim row above the chat surface, visible only on mobile.
 *
 * On desktop the conversation sidebar lives permanently in the layout; on
 * mobile we collapse it into a drawer triggered from this bar. The trailing
 * slot lets a specific chat surface (e.g. an existing conversation) add an
 * Export button without us duplicating header machinery per page.
 */
export function ChatMobileBar({ activeId, trailing }: ChatMobileBarProps) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-border bg-background px-3 py-2 md:hidden">
      <MobileConversationDrawer activeId={activeId} />
      {trailing && <div className="flex items-center gap-1">{trailing}</div>}
    </div>
  );
}
