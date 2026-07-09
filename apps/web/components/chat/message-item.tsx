"use client";

import { useEffect, useState } from "react";
import {
  AlertCircle,
  BookmarkPlus,
  Brain,
  ChevronLeft,
  ChevronRight,
  Globe,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Telescope,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import type { DisplayMessage, MessageError } from "@/hooks/use-chat";
import type { MessageFeedback, QueryIntent } from "@/lib/types";
import { Citations } from "./citations";
import { CompetitorWarningBanner } from "./competitor-warning-banner";
import { ConfidenceBadge } from "./confidence-badge";
import { CopyButton } from "./copy-button";
import { CreateTemplateDialog } from "./create-template-dialog";
import { ExportGDocsButton } from "./export-gdocs-button";
import { ExportNotionButton } from "./export-notion-button";
import { SubmitApprovalButton } from "./submit-approval-button";
import { Markdown } from "./markdown";
import {
  MeetingPrepRequestCard,
  parseMeetingPrepPrompt,
} from "./meeting-prep-request";
import { PostSlackButton } from "./post-slack-button";
import { SearchingIndicator } from "./searching-indicator";
import { SendGmailButton } from "./send-gmail-button";
import { ShareButton } from "./share-button";
import { cn } from "@/lib/utils";

/**
 * Heuristic: does this assistant turn look like an email draft?
 *  - It must be a writing-mode turn (intent === task_generation).
 *  - The body should either start with a "Subject:" line or contain a
 *    salutation+sign-off pair. We keep the test loose because the AI's
 *    output varies; false positives just mean the user sees a Gmail button
 *    they can ignore.
 */
function looksLikeEmail(intent: QueryIntent | null, body: string): boolean {
  if (intent !== "task_generation") return false;
  if (/^\s*(\*\*)?subject\s*:/i.test(body)) return true;
  const hasSalutation = /\b(hi|hello|dear|hey)\b[^\n]*,/i.test(body.slice(0, 200));
  const hasSignoff =
    /\b(best|regards|cheers|thanks|sincerely|warmly)\b[^\n]{0,50}(,|\n)/i.test(
      body.slice(-300),
    );
  return hasSalutation && hasSignoff;
}

/**
 * Heuristic title — strip markdown noise and quote characters off the first
 * non-empty line, cap at 80 chars. Used to pre-fill the title field in the
 * Notion / Google Docs export dialogs. Falls back to "Untitled" so the
 * dialog never opens with an empty input.
 */
function deriveTitle(body: string): string {
  const first = body
    .split("\n")
    .map((line) => line.trim())
    .find((line) => line.length > 0);
  if (!first) return "Untitled";
  const cleaned = first
    .replace(/^#+\s*/, "")
    .replace(/^\*+\s*/, "")
    .replace(/^subject\s*:\s*/i, "")
    .replace(/^["'`]+|["'`]+$/g, "")
    .trim();
  return cleaned.length > 80 ? `${cleaned.slice(0, 77)}…` : cleaned || "Untitled";
}

interface MessageItemProps {
  message: DisplayMessage;
  isLast: boolean;
  onRetry?: (assistantLocalId: string) => void;
  onFeedback?: (assistantLocalId: string, feedback: MessageFeedback) => void;
  onRegenerate?: (assistantLocalId: string, refinement?: string) => void;
  onSwitchBranch?: (assistantLocalId: string, branchIndex: number) => void;
  /** Production Roadmap 1.5 — re-run a low-confidence answer with different
   *  retrieval strategy. The two buttons are rendered automatically when
   *  this callback is supplied AND confidence.level === "low". */
  onRetryWithMode?: (
    assistantLocalId: string,
    mode: "broader" | "deeper",
  ) => void;
  /** When a stream is in flight elsewhere, disable regenerate so we don't
   *  fire concurrent SSE streams from the same hook. */
  streamingDisabled?: boolean;
  /** The user message that produced this assistant turn — used to pre-fill
   *  "save as template". Undefined on user bubbles. */
  priorUserText?: string;
}

const INTENT_LABELS: Record<QueryIntent, string> = {
  factual_qa: "Answer mode",
  task_generation: "Writing mode",
  analysis: "Analysis mode",
  search: "Search mode",
  // Production Roadmap 1.9 — Quick Answer: bounded retrieval for short
  // factual lookups. The badge label is the user-visible cue that this
  // turn ran on the fast path.
  quick_answer: "Quick answer",
};

export function MessageItem({
  message,
  isLast,
  onRetry,
  onFeedback,
  onRegenerate,
  onSwitchBranch,
  onRetryWithMode,
  streamingDisabled,
  priorUserText,
}: MessageItemProps) {
  const [saveTemplateOpen, setSaveTemplateOpen] = useState(false);
  const [showRefinement, setShowRefinement] = useState(false);
  const [refinement, setRefinement] = useState("");
  // `id="m-{server_id}"` powers deep links from the admin /admin/feedback
  // page and the threshold-alert email straight to a specific message.
  const anchorId = message.server_id ? `m-${message.server_id}` : undefined;
  if (message.role === "user") {
    const meetingPrep = parseMeetingPrepPrompt(message.content);
    return (
      <div id={anchorId} className="flex justify-end px-1 scroll-mt-24">
        <div className="max-w-[80%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm leading-6 text-primary-foreground shadow-sm">
          {meetingPrep ? (
            <MeetingPrepRequestCard parsed={meetingPrep} raw={message.content} />
          ) : (
            <div className="whitespace-pre-wrap break-words">{message.content}</div>
          )}
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
    <div id={anchorId} className="group flex gap-3 px-1 scroll-mt-24">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-tint text-brand">
        <Brain className="h-3.5 w-3.5" />
      </div>

      <div className="min-w-0 flex-1">
        {expandedSearches && <SearchingIndicator searches={message.searches} />}

        {!expandedSearches && showSearchPanel && (
          <SearchSummary count={message.searches.length} />
        )}

        {message.intent && (isStreaming || !hasContent) && (
          <div className="mb-1.5 inline-flex items-center gap-1 rounded-full bg-brand-tint px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wide text-brand">
            {INTENT_LABELS[message.intent]}
          </div>
        )}

        {message.confidence && !isError && (
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <ConfidenceBadge confidence={message.confidence} />
            {/* Production Roadmap 1.5 — Answer Improvement. Surfaces two
                retry buttons on low-confidence answers so users have an
                escalation path short of rewording the question. We don't
                expose them on medium/high (low signal-to-noise) or while
                streaming (the retry would race the in-flight turn). */}
            {message.confidence.level === "low" &&
              !isStreaming &&
              !isError &&
              hasContent &&
              onRetryWithMode &&
              message.server_id && (
                <RetryWithModeButtons
                  disabled={streamingDisabled}
                  onBroader={() => onRetryWithMode(message.local_id, "broader")}
                  onDeeper={() => onRetryWithMode(message.local_id, "deeper")}
                />
              )}
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

        {message.competitor_matches.length > 0 && !isError && (
          <CompetitorWarningBanner matches={message.competitor_matches} />
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
              (isLast || message.feedback || message.total_branches > 1) &&
                "opacity-100",
            )}
          >
            {message.total_branches > 1 && onSwitchBranch && (
              <BranchNavigator
                currentIndex={message.active_branch_index}
                total={message.total_branches}
                onPrev={() =>
                  onSwitchBranch(
                    message.local_id,
                    Math.max(0, message.active_branch_index - 1),
                  )
                }
                onNext={() =>
                  onSwitchBranch(
                    message.local_id,
                    Math.min(
                      message.total_branches - 1,
                      message.active_branch_index + 1,
                    ),
                  )
                }
              />
            )}
            <CopyButton text={message.content} messageId={message.server_id} />
            {message.server_id && looksLikeEmail(message.intent, message.content) && (
              <SendGmailButton
                messageId={message.server_id}
                body={message.content}
                competitorMatches={message.competitor_matches}
              />
            )}
            {message.server_id && (
              <PostSlackButton
                messageId={message.server_id}
                body={message.content}
                competitorMatches={message.competitor_matches}
              />
            )}
            {message.server_id && (
              <ExportNotionButton
                messageId={message.server_id}
                body={message.content}
                suggestedTitle={deriveTitle(message.content)}
                competitorMatches={message.competitor_matches}
              />
            )}
            {message.server_id && (
              <ExportGDocsButton
                messageId={message.server_id}
                body={message.content}
                suggestedTitle={deriveTitle(message.content)}
                competitorMatches={message.competitor_matches}
              />
            )}
            {message.server_id && (
              <SubmitApprovalButton
                messageId={message.server_id}
                body={message.content}
                competitorMatches={message.competitor_matches}
              />
            )}
            {priorUserText && (
              <button
                type="button"
                onClick={() => setSaveTemplateOpen(true)}
                className="tap inline-flex items-center justify-center rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                aria-label="Save prompt as template"
                title="Save prompt as template"
              >
                <BookmarkPlus className="h-3.5 w-3.5" />
              </button>
            )}
            {onRegenerate && message.server_id && (
              <button
                type="button"
                onClick={() => setShowRefinement((v) => !v)}
                disabled={streamingDisabled}
                className="tap inline-flex items-center justify-center rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="Regenerate response"
                title="Regenerate response"
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </button>
            )}
            {message.server_id && (
              <ShareButton messageId={message.server_id} />
            )}
            {onFeedback && message.server_id && (
              <FeedbackButtons
                feedback={message.feedback}
                onSelect={(value) => onFeedback(message.local_id, value)}
              />
            )}
          </div>
        )}

        {showRefinement && onRegenerate && message.server_id && (
          <div className="mt-2 flex items-stretch gap-1.5">
            <input
              value={refinement}
              onChange={(e) => setRefinement(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  onRegenerate(message.local_id, refinement.trim() || undefined);
                  setRefinement("");
                  setShowRefinement(false);
                } else if (e.key === "Escape") {
                  setShowRefinement(false);
                }
              }}
              placeholder="Optional: make it more formal, focus on X, shorter…"
              className="flex-1 rounded-md border border-input bg-background px-2.5 py-1.5 text-xs outline-none ring-offset-background focus:ring-1 focus:ring-ring"
              autoFocus
            />
            <button
              type="button"
              onClick={() => {
                onRegenerate(message.local_id, refinement.trim() || undefined);
                setRefinement("");
                setShowRefinement(false);
              }}
              disabled={streamingDisabled}
              className="rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              Regenerate
            </button>
            <button
              type="button"
              onClick={() => {
                setShowRefinement(false);
                setRefinement("");
              }}
              className="tap inline-flex items-center justify-center rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label="Cancel"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>
      {priorUserText && (
        <CreateTemplateDialog
          open={saveTemplateOpen}
          onOpenChange={setSaveTemplateOpen}
          initialText={priorUserText}
        />
      )}
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
          "tap inline-flex items-center justify-center rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
          feedback === "positive" && "bg-success-tint text-success hover:bg-success/15 hover:text-success",
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
          "tap inline-flex items-center justify-center rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
          feedback === "negative" && "bg-destructive/10 text-destructive hover:bg-destructive/15",
        )}
      >
        <ThumbsDown className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function BranchNavigator({
  currentIndex,
  total,
  onPrev,
  onNext,
}: {
  currentIndex: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  const atStart = currentIndex <= 0;
  const atEnd = currentIndex >= total - 1;
  return (
    <div className="mr-1 flex items-center gap-0.5 rounded-md bg-muted/60 px-1">
      <button
        type="button"
        onClick={onPrev}
        disabled={atStart}
        className="tap inline-flex items-center justify-center rounded p-0.5 text-muted-foreground transition-colors hover:bg-background disabled:opacity-40"
        aria-label="Previous response"
        title="Previous response"
      >
        <ChevronLeft className="h-3.5 w-3.5" />
      </button>
      <span className="px-0.5 text-[10px] font-medium tabular-nums text-muted-foreground">
        {currentIndex + 1}/{total}
      </span>
      <button
        type="button"
        onClick={onNext}
        disabled={atEnd}
        className="tap inline-flex items-center justify-center rounded p-0.5 text-muted-foreground transition-colors hover:bg-background disabled:opacity-40"
        aria-label="Next response"
        title="Next response"
      >
        <ChevronRight className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function RetryWithModeButtons({
  disabled,
  onBroader,
  onDeeper,
}: {
  disabled?: boolean;
  onBroader: () => void;
  onDeeper: () => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        onClick={onBroader}
        disabled={disabled}
        className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
        title="Re-run this prompt against your full knowledge base (ignore scope)."
      >
        <Globe className="h-3 w-3" /> Search broader
      </button>
      <button
        type="button"
        onClick={onDeeper}
        disabled={disabled}
        className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
        title="Re-run with a deeper search (more retrievals, more rounds)."
      >
        <Telescope className="h-3 w-3" /> Search deeper
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
  // V4 #79 — moderation blocks are policy refusals, not server faults. Render
  // them in amber with a shield icon so the user reads "this query wasn't run"
  // rather than "the server broke" — and so retry is suppressed (the same
  // input will just re-trip the catalog).
  const isModerated = error.code === "moderation_blocked";

  if (isModerated) {
    return (
      <div className="space-y-2 rounded-xl border border-amber/20 bg-amber-tint px-3 py-2.5">
        <div className="flex items-start gap-2 text-sm text-amber">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber" />
          <span>{error.message}</span>
        </div>
        {error.request_id && (
          <div className="pl-6 text-xs text-amber/70">
            ref <code className="font-mono">{error.request_id}</code>
          </div>
        )}
      </div>
    );
  }

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
