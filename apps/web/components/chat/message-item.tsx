"use client";

import { useEffect, useState } from "react";
import {
  AlertCircle,
  Brain,
  Loader2,
  RefreshCw,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import type { DisplayMessage, MessageError } from "@/hooks/use-chat";
import type { MessageFeedback } from "@/lib/types";
import { Citations } from "./citations";
import { ConfidenceBadge } from "./confidence-badge";
import { CopyButton } from "./copy-button";
import { Markdown } from "./markdown";
import { SearchingIndicator } from "./searching-indicator";
import { cn } from "@/lib/utils";

interface MessageItemProps {
  message: DisplayMessage;
  isLast: boolean;
  onRetry?: (assistantLocalId: string) => void;
  onFeedback?: (assistantLocalId: string, feedback: MessageFeedback) => void;
}

export function MessageItem({
  message,
  isLast,
  onRetry,
  onFeedback,
}: MessageItemProps) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end px-1">
        <div className="max-w-[80%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm leading-6 text-primary-foreground shadow-sm">
          <div className="whitespace-pre-wrap break-words">{message.content}</div>
        </div>
      </div>
    );
  }

  const isStreaming = message.status === "streaming";
  const isError = message.status === "error";
  const isAborted = message.status === "aborted";
  const hasContent = message.content.length > 0;
  const showSearchPanel = message.searches.length > 0;
  const expandedSearches = isStreaming && !hasContent;

  return (
    <div className="group flex gap-3 px-1">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Brain className="h-3.5 w-3.5" />
      </div>

      <div className="min-w-0 flex-1">
        {expandedSearches && <SearchingIndicator searches={message.searches} />}

        {!expandedSearches && showSearchPanel && (
          <SearchSummary count={message.searches.length} />
        )}

        {message.confidence && !isError && (
          <div className="mb-2">
            <ConfidenceBadge confidence={message.confidence} />
          </div>
        )}

        {isError && message.error ? (
          <ErrorPanel
            error={message.error}
            canRetry={Boolean(onRetry) && Boolean(message.pending_text)}
            onRetry={() => onRetry?.(message.local_id)}
          />
        ) : hasContent ? (
          <Markdown>{message.content}</Markdown>
        ) : isStreaming ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Thinking…
          </div>
        ) : (
          <div className="text-sm italic text-muted-foreground">No response.</div>
        )}

        {isAborted && hasContent && (
          <p className="mt-2 text-[11px] italic text-muted-foreground">
            Stopped by you.
          </p>
        )}

        {message.sources.length > 0 && !isStreaming && !isError && (
          <Citations sources={message.sources} />
        )}

        {!isStreaming && !isError && hasContent && (
          <div
            className={cn(
              "mt-2 flex items-center gap-1 opacity-0 transition-opacity",
              "group-hover:opacity-100",
              // Always show the action row if the user has already rated —
              // otherwise the thumb state would silently disappear on mouse-out.
              (isLast || message.feedback) && "opacity-100",
            )}
          >
            <CopyButton text={message.content} />
            {onFeedback && message.server_id && (
              <FeedbackButtons
                feedback={message.feedback}
                onSelect={(value) => onFeedback(message.local_id, value)}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Tri-state thumbs. Clicking the same icon twice clears the rating. Disabled
 * (server_id not yet known) is handled by the parent — we only render once
 * the assistant turn has persisted.
 */
function FeedbackButtons({
  feedback,
  onSelect,
}: {
  feedback: MessageFeedback | null;
  onSelect: (value: MessageFeedback) => void;
}) {
  return (
    <div className="ml-1 flex items-center gap-0.5">
      <button
        type="button"
        onClick={() => onSelect("positive")}
        aria-pressed={feedback === "positive"}
        aria-label={feedback === "positive" ? "Remove positive rating" : "Mark helpful"}
        className={cn(
          "inline-flex items-center justify-center rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
          feedback === "positive" && "bg-emerald-500/10 text-emerald-600 hover:bg-emerald-500/15 hover:text-emerald-700 dark:text-emerald-400 dark:hover:text-emerald-300",
        )}
      >
        <ThumbsUp className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        onClick={() => onSelect("negative")}
        aria-pressed={feedback === "negative"}
        aria-label={feedback === "negative" ? "Remove negative rating" : "Mark not helpful"}
        className={cn(
          "inline-flex items-center justify-center rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
          feedback === "negative" && "bg-destructive/10 text-destructive hover:bg-destructive/15",
        )}
      >
        <ThumbsDown className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function SearchSummary({ count }: { count: number }) {
  return (
    <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
      Drew from {count} {count === 1 ? "search" : "searches"}
    </div>
  );
}

/**
 * Inline error block for a failed assistant turn. Three variants:
 *   - rate_limited / quota_exceeded → countdown, retry disabled until 0
 *   - quota_exceeded with no retry_after → upgrade-flavored copy
 *   - everything else → generic message + Retry
 *
 * Lives in the assistant bubble (not a toast) because the user needs to see
 * the error in the conversation flow, not lose it to a 5-second fade-out.
 */
function ErrorPanel({
  error,
  canRetry,
  onRetry,
}: {
  error: MessageError;
  canRetry: boolean;
  onRetry: () => void;
}) {
  const isRateLimit = error.code === "rate_limited";
  const isQuota = error.code === "quota_exceeded";

  return (
    <div className="space-y-2 rounded-lg border border-destructive/30 bg-destructive-soft/60 px-3 py-2.5">
      <div className="flex items-start gap-2 text-sm text-destructive">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
        <span>{error.message}</span>
      </div>

      <div className="flex items-center gap-3 pl-6 text-xs">
        {isRateLimit && error.retry_after ? (
          <Countdown
            seconds={error.retry_after}
            canRetry={canRetry}
            onRetry={onRetry}
          />
        ) : isQuota ? (
          <a
            href="/settings"
            className="font-medium text-destructive underline-offset-2 hover:underline"
          >
            Manage plan
          </a>
        ) : canRetry ? (
          <button
            onClick={onRetry}
            className="inline-flex items-center gap-1 font-medium text-destructive underline-offset-2 hover:underline"
          >
            <RefreshCw className="h-3 w-3" />
            Retry
          </button>
        ) : null}

        {error.request_id && (
          <span className="text-muted-foreground/80">
            ref <code className="font-mono">{error.request_id}</code>
          </span>
        )}
      </div>
    </div>
  );
}

function Countdown({
  seconds,
  canRetry,
  onRetry,
}: {
  seconds: number;
  canRetry: boolean;
  onRetry: () => void;
}) {
  const [remaining, setRemaining] = useState(seconds);

  useEffect(() => {
    if (remaining <= 0) return;
    const t = setTimeout(() => setRemaining((r) => Math.max(0, r - 1)), 1000);
    return () => clearTimeout(t);
  }, [remaining]);

  if (remaining > 0) {
    return (
      <span className="text-muted-foreground">
        Try again in <span className="font-medium tabular-nums">{remaining}s</span>
      </span>
    );
  }

  return (
    <button
      onClick={onRetry}
      disabled={!canRetry}
      className="inline-flex items-center gap-1 font-medium text-destructive underline-offset-2 hover:underline disabled:opacity-50"
    >
      <RefreshCw className="h-3 w-3" />
      Retry
    </button>
  );
}
