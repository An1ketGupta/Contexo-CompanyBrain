"use client";

import { useEffect, useLayoutEffect, useRef } from "react";
import type { DisplayMessage } from "@/hooks/use-chat";
import type { MessageFeedback } from "@/lib/types";
import { MessageItem } from "./message-item";

interface MessageListProps {
  messages: DisplayMessage[];
  onRetry?: (assistantLocalId: string) => void;
  onFeedback?: (assistantLocalId: string, feedback: MessageFeedback) => void;
  onRegenerate?: (assistantLocalId: string, refinement?: string) => void;
  onSwitchBranch?: (assistantLocalId: string, branchIndex: number) => void;
  isStreaming?: boolean;
}

export function MessageList({
  messages,
  onRetry,
  onFeedback,
  onRegenerate,
  onSwitchBranch,
  isStreaming,
}: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const lastLengthRef = useRef(0);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      stickToBottomRef.current = distance < 160;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const grew = messages.length > lastLengthRef.current;
    lastLengthRef.current = messages.length;
    if (stickToBottomRef.current || grew) {
      el.scrollTop = el.scrollHeight;
    }
  });

  return (
    <div
      ref={containerRef}
      className="flex-1 overflow-y-auto"
      style={{ scrollbarGutter: "stable" }}
    >
      <div className="mx-auto max-w-3xl space-y-6 px-4 py-6 md:px-6">
        {messages.map((m, i) => {
          // Find the most recent user message above this one — that's the
          // prompt that produced this assistant response, and the actual
          // reusable artifact the user might want to save as a template.
          let priorUserText: string | undefined;
          if (m.role === "assistant") {
            for (let j = i - 1; j >= 0; j--) {
              if (messages[j].role === "user") {
                priorUserText = messages[j].content;
                break;
              }
            }
          }
          return (
            <MessageItem
              key={m.local_id}
              message={m}
              isLast={i === messages.length - 1}
              onRetry={onRetry}
              onFeedback={onFeedback}
              onRegenerate={onRegenerate}
              onSwitchBranch={onSwitchBranch}
              streamingDisabled={!!isStreaming}
              priorUserText={priorUserText}
            />
          );
        })}
      </div>
    </div>
  );
}
