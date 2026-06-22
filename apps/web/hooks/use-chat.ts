"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { mutate as globalMutate } from "swr";
import { networkError, parseApiError, type ApiError, type ErrorCode } from "@/lib/errors";
import { newRequestId, REQUEST_ID_HEADER } from "@/lib/request-id";
import type {
  ChatStreamEvent,
  CompetitorMatch,
  MessageConfidence,
  MessageFeedback,
  MessageSource,
  QueryIntent,
} from "@/lib/types";
import type { PersistedBranch } from "./use-conversation";

export interface KnowledgeGap {
  topics: string[];
  /** Server timestamp when the gap was detected — used as a React key for animation. */
  detected_at: number;
}

export interface SearchProgress {
  query: string;
  status: "running" | "done";
  hit_count: number | null;
}

export type DisplayRole = "user" | "assistant";

export interface MessageError {
  code: ErrorCode;
  message: string;
  request_id?: string;
  retry_after?: number;
}

/** One inactive sibling of an assistant turn — captured so the branch
 *  navigator can flip back without a round trip. The active branch's data
 *  always lives on the top-level DisplayMessage; this holds the rest. */
export interface InactiveBranch {
  server_id: string | null;
  content: string;
  sources: MessageSource[];
  feedback: MessageFeedback | null;
  confidence: MessageConfidence | null;
  intent: QueryIntent | null;
  competitor_matches: CompetitorMatch[];
}

export interface DisplayMessage {
  /**
   * Local id only — the real DB id arrives on the `done` event. We use the
   * local id as a React key so streaming doesn't remount the bubble.
   */
  local_id: string;
  server_id: string | null;
  role: DisplayRole;
  content: string;
  sources: MessageSource[];
  searches: SearchProgress[];
  status: "streaming" | "complete" | "error" | "aborted";
  // Tri-state thumb rating. Tracked on the assistant bubble; user bubbles leave it null.
  feedback: MessageFeedback | null;
  // Confidence band attached by the orchestrator once retrieval completes.
  // Null on user bubbles and on assistant turns that didn't trigger search.
  confidence: MessageConfidence | null;
  // V3 Day 4 #51 — which prompt mode the orchestrator picked. UI shows
  // "Writing mode" / "Analysis mode" while the answer streams.
  intent: QueryIntent | null;
  // Competitor watchlist hits flagged by the post-generation detector.
  // Empty array on user bubbles and on clean assistant turns. Drives the
  // inline warning banner and the export-confirm dialogs.
  competitor_matches: CompetitorMatch[];
  error?: MessageError;
  /**
   * The text we sent to produce this assistant message — kept on the user
   * bubble in `pending_text` for retry. Carried on the assistant message
   * itself so retrying a failed assistant turn re-uses the same prompt.
   */
  pending_text?: string;
  /**
   * Client-minted UUID. Sent with every attempt so the backend can dedupe
   * retries (the user-message row uses this as its primary key).
   */
  client_message_id?: string;
  created_at: string;
  // ── V3 Day 3 #42: branching ────────────────────────────────────────────
  // Server id of the user message that produced this assistant turn. Used
  // to drive the regenerate flow (the FastAPI endpoint reverse-looks-up
  // the prompt from this id).
  parent_user_message_id?: string | null;
  // Index of the branch currently shown (0 = original, 1 = first regen, ...).
  active_branch_index: number;
  // Total branches that exist for this turn. UI shows a navigator when > 1.
  total_branches: number;
  // Inactive siblings, keyed by branch_index. The active branch's content
  // is on the top-level message; switching just swaps these.
  other_branches: Record<number, InactiveBranch>;
}

export interface UseChatOptions {
  conversationId: string | null;
  /**
   * Pin every search to a single document. Only honoured on conversation
   * creation; ignored once a conversation_id exists on the server.
   */
  scopedDocumentId?: string | null;
  /**
   * Pin every search to documents that carry any of these tags. Same
   * lifecycle as scopedDocumentId — only honoured on the first send of a
   * brand-new conversation. The two scope mechanisms are mutually exclusive
   * in the UI (single-doc scope wins).
   */
  scopedTags?: string[];
  /**
   * V5 #35 — Pick a saved Collection by id; the backend resolves it to the
   * collection's current tag_filters and reuses scoped_tags plumbing. Same
   * "only on first send" lifecycle. Wins over scopedTags when both are set.
   */
  scopedCollectionId?: string | null;
  initialMessages?: Array<{
    id: string;
    role: DisplayRole;
    content: string;
    sources: MessageSource[] | null;
    feedback?: MessageFeedback | null;
    confidence?: MessageConfidence | null;
    intent?: QueryIntent | null;
    competitor_matches?: CompetitorMatch[] | null;
    created_at: string;
    parent_user_message_id?: string | null;
    branch_index?: number;
    total_branches?: number;
  }>;
  /**
   * Inactive branches keyed by parent_user_message_id. Loaded from the
   * conversation detail response so the navigator can flip back to an
   * older regeneration without a fresh round trip.
   */
  initialBranches?: Record<string, PersistedBranch[]>;
  onConversationStarted?: (id: string) => void;
  onTurnComplete?: (conversationId: string) => void;
}

// If we haven't received a token or progress event in this long, the stream
// is probably stalled (Vercel/Nginx silently dropped it). Surface as error
// so the user gets a Retry button instead of an infinite "Thinking…".
const STREAM_STALL_TIMEOUT_MS = 60_000;

function makeLocalId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function makeClientMessageId(): string {
  // We pass this to the backend as the user-message row's primary key, which
  // is uuid-typed. crypto.randomUUID is universally available in our target
  // runtimes (modern browsers, Node 19+, edge). We don't ship a polyfill —
  // if it's missing, that's a "your browser is too old to use this app"
  // situation that the team will notice in Sentry.
  return crypto.randomUUID();
}

function persistedToDisplay(
  m: NonNullable<UseChatOptions["initialMessages"]>[number],
  branchesByParent: Record<string, PersistedBranch[]> = {},
): DisplayMessage {
  const parentId = m.parent_user_message_id ?? null;
  const siblings = parentId ? branchesByParent[parentId] ?? [] : [];
  const activeIdx = m.branch_index ?? 0;
  const totalBranches = m.total_branches ?? (siblings.length || 1);
  const other: Record<number, InactiveBranch> = {};
  for (const b of siblings) {
    if (b.branch_index === activeIdx) continue;
    other[b.branch_index] = {
      server_id: b.id,
      content: b.content,
      sources: b.sources ?? [],
      feedback: b.feedback,
      confidence: b.metadata?.confidence ?? null,
      intent: b.metadata?.intent ?? null,
      competitor_matches: b.competitor_matches ?? [],
    };
  }
  return {
    local_id: m.id,
    server_id: m.id,
    role: m.role,
    content: m.content,
    sources: m.sources ?? [],
    searches: [],
    status: "complete",
    feedback: m.feedback ?? null,
    confidence: m.confidence ?? null,
    intent: m.intent ?? null,
    competitor_matches: m.competitor_matches ?? [],
    created_at: m.created_at,
    parent_user_message_id: parentId,
    active_branch_index: activeIdx,
    total_branches: totalBranches,
    other_branches: other,
  };
}

export function useChat({
  conversationId,
  scopedDocumentId,
  scopedTags,
  scopedCollectionId,
  initialMessages,
  initialBranches,
  onConversationStarted,
  onTurnComplete,
}: UseChatOptions) {
  const [messages, setMessages] = useState<DisplayMessage[]>(() =>
    (initialMessages ?? []).map((m) =>
      persistedToDisplay(m, initialBranches ?? {}),
    ),
  );
  const [isStreaming, setIsStreaming] = useState(false);
  const [currentConvoId, setCurrentConvoId] = useState<string | null>(
    conversationId,
  );
  // Server signals when retrieval returned zero context across all searches.
  // We surface it as a transient banner above the chat input; cleared on the
  // next send so it doesn't linger past the user acknowledging it.
  const [knowledgeGap, setKnowledgeGap] = useState<KnowledgeGap | null>(null);
  const dismissKnowledgeGap = useCallback(() => setKnowledgeGap(null), []);

  const abortRef = useRef<AbortController | null>(null);
  const convoIdRef = useRef<string | null>(conversationId);
  useEffect(() => {
    convoIdRef.current = currentConvoId;
  }, [currentConvoId]);

  const lastConvoIdRef = useRef<string | null>(conversationId);
  useEffect(() => {
    if (lastConvoIdRef.current !== conversationId) {
      lastConvoIdRef.current = conversationId;
      setCurrentConvoId(conversationId);
      setMessages(
        (initialMessages ?? []).map((m) =>
          persistedToDisplay(m, initialBranches ?? {}),
        ),
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  useEffect(() => {
    if (!initialMessages || initialMessages.length === 0) return;
    setMessages((prev) => {
      if (prev.length > 0) return prev;
      return initialMessages.map((m) =>
        persistedToDisplay(m, initialBranches ?? {}),
      );
    });
  }, [initialMessages, initialBranches]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  /**
   * Drive a single chat turn. If `existingAssistantLocalId` is provided, we
   * reuse that bubble instead of appending fresh user+assistant rows —
   * that's the retry path.
   */
  const runTurn = useCallback(
    async (
      text: string,
      existing: {
        assistantLocalId: string;
        userLocalId: string;
        clientMessageId: string;
      } | null,
    ): Promise<void> => {
      const trimmed = text.trim();
      if (!trimmed || isStreaming) return;

      const clientMessageId = existing?.clientMessageId ?? makeClientMessageId();
      const userLocalId = existing?.userLocalId ?? makeLocalId("u");
      const assistantLocalId = existing?.assistantLocalId ?? makeLocalId("a");

      // A new send means whatever gap the last turn surfaced is no longer
      // about this conversation. Clear before we start.
      setKnowledgeGap(null);

      if (existing) {
        // Retry: reset the assistant bubble's transient state.
        setMessages((prev) =>
          prev.map((m) =>
            m.local_id === assistantLocalId
              ? {
                  ...m,
                  content: "",
                  sources: [],
                  searches: [],
                  confidence: null,
                  status: "streaming",
                  error: undefined,
                }
              : m,
          ),
        );
      } else {
        const userMsg: DisplayMessage = {
          local_id: userLocalId,
          server_id: null,
          role: "user",
          content: trimmed,
          sources: [],
          searches: [],
          status: "complete",
          feedback: null,
          confidence: null,
          intent: null,
          competitor_matches: [],
          pending_text: trimmed,
          client_message_id: clientMessageId,
          created_at: new Date().toISOString(),
          active_branch_index: 0,
          total_branches: 1,
          other_branches: {},
        };
        const assistantMsg: DisplayMessage = {
          local_id: assistantLocalId,
          server_id: null,
          role: "assistant",
          content: "",
          sources: [],
          searches: [],
          status: "streaming",
          feedback: null,
          confidence: null,
          intent: null,
          competitor_matches: [],
          pending_text: trimmed,
          client_message_id: clientMessageId,
          created_at: new Date().toISOString(),
          active_branch_index: 0,
          total_branches: 1,
          other_branches: {},
        };
        setMessages((prev) => [...prev, userMsg, assistantMsg]);
      }

      setIsStreaming(true);
      const controller = new AbortController();
      abortRef.current = controller;

      // Stall watchdog — bumped on every server event.
      let stallTimer: ReturnType<typeof setTimeout> | null = null;
      const armStall = () => {
        if (stallTimer) clearTimeout(stallTimer);
        stallTimer = setTimeout(() => controller.abort("stall"), STREAM_STALL_TIMEOUT_MS);
      };
      armStall();

      try {
        let res: Response;
        try {
          res = await fetch("/api/chat/stream", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              [REQUEST_ID_HEADER]: newRequestId(),
            },
            body: JSON.stringify({
              message: trimmed,
              conversation_id: convoIdRef.current,
              client_message_id: clientMessageId,
              // Backend ignores this once the conversation row exists; only
              // honoured on the very first send of a brand-new conversation.
              scoped_document_id:
                !convoIdRef.current && scopedDocumentId ? scopedDocumentId : undefined,
              // Same lifecycle as scoped_document_id — only sent on the very
              // first POST of a fresh conversation; backend ignores it once
              // the conversation row exists.
              scoped_tags:
                !convoIdRef.current && scopedTags && scopedTags.length > 0
                  ? scopedTags
                  : undefined,
              // V5 #35 — collection wins over scoped_tags when both are
              // present (backend hard-codes that priority too). Only sent on
              // a brand-new conversation; backend ignores it once persisted.
              scoped_collection_id:
                !convoIdRef.current && scopedCollectionId ? scopedCollectionId : undefined,
            }),
            signal: controller.signal,
          });
        } catch (err) {
          throw networkError(err);
        }

        if (!res.ok || !res.body) {
          throw await parseApiError(res);
        }

        await consumeSseStream(res.body, (event) => {
          armStall();
          handleEvent(event, assistantLocalId, {
            setMessages,
            setCurrentConvoId,
            setKnowledgeGap,
            onConversationStarted,
          });
        });

        // End of stream without an explicit error event.
        setMessages((prev) =>
          prev.map((m) =>
            m.local_id === assistantLocalId && m.status === "streaming"
              ? { ...m, status: "complete" }
              : m,
          ),
        );

        const finalConvoId = convoIdRef.current;
        if (finalConvoId) onTurnComplete?.(finalConvoId);

        // Tick the quota meter forward without waiting for the 60s poll.
        // SWR de-dupes if no QuotaMeter is mounted, so this is free.
        globalMutate("/api/usage/me");
      } catch (err) {
        const abortReason = (controller.signal as { reason?: unknown }).reason;
        const isAbort = (err as Error)?.name === "AbortError";

        if (isAbort && abortReason !== "stall") {
          setMessages((prev) =>
            prev.map((m) =>
              m.local_id === assistantLocalId
                ? {
                    ...m,
                    status: "aborted",
                    content: m.content || "(Stopped)",
                  }
                : m,
            ),
          );
        } else {
          const apiErr: ApiError =
            isAbort && abortReason === "stall"
              ? {
                  code: "stream_interrupted",
                  status: 0,
                  message:
                    "The stream stalled — the connection probably dropped. Try again.",
                }
              : (err as ApiError);

          setMessages((prev) =>
            prev.map((m) =>
              m.local_id === assistantLocalId
                ? {
                    ...m,
                    status: "error",
                    error: {
                      code: apiErr.code,
                      message: apiErr.message,
                      request_id: apiErr.request_id,
                      retry_after: apiErr.retry_after,
                    },
                  }
                : m,
            ),
          );
        }
      } finally {
        if (stallTimer) clearTimeout(stallTimer);
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [
      isStreaming,
      onConversationStarted,
      onTurnComplete,
      scopedDocumentId,
      scopedTags,
      scopedCollectionId,
    ],
  );

  const send = useCallback(
    (rawText: string) => runTurn(rawText, null),
    [runTurn],
  );

  /**
   * Re-run a failed assistant turn using the same client_message_id so the
   * backend's idempotent persistence dedupes the user-message row.
   */
  const retry = useCallback(
    async (assistantLocalId: string) => {
      const target = messages.find((m) => m.local_id === assistantLocalId);
      if (!target || target.status !== "error" || !target.pending_text) return;

      // Find the matching user bubble (same client_message_id) so we can
      // reuse its localId for the message timeline.
      const userBubble = messages.find(
        (m) =>
          m.role === "user" && m.client_message_id === target.client_message_id,
      );

      await runTurn(target.pending_text, {
        assistantLocalId,
        userLocalId: userBubble?.local_id ?? makeLocalId("u"),
        clientMessageId: target.client_message_id ?? makeClientMessageId(),
      });
    },
    [messages, runTurn],
  );

  /**
   * V3 Day 3 #42 — regenerate the active branch in place.
   *
   * Stashes the current top-level branch into other_branches, sets
   * status=streaming with empty content, then drives a fresh SSE stream
   * against POST /api/chat/messages/{id}/regenerate. On success the new
   * branch becomes active and total_branches bumps by 1.
   */
  const regenerate = useCallback(
    async (assistantLocalId: string, refinement?: string): Promise<void> => {
      if (isStreaming) return;
      const target = messages.find((m) => m.local_id === assistantLocalId);
      if (!target || target.role !== "assistant" || !target.server_id) return;

      const previousBranchIndex = target.active_branch_index;
      const previousSnapshot: InactiveBranch = {
        server_id: target.server_id,
        content: target.content,
        sources: target.sources,
        feedback: target.feedback,
        confidence: target.confidence,
        intent: target.intent,
        competitor_matches: target.competitor_matches,
      };

      setMessages((prev) =>
        prev.map((m) =>
          m.local_id === assistantLocalId
            ? {
                ...m,
                status: "streaming",
                content: "",
                sources: [],
                searches: [],
                feedback: null,
                confidence: null,
                intent: null,
                competitor_matches: [],
                error: undefined,
                server_id: null,
                other_branches: {
                  ...m.other_branches,
                  [previousBranchIndex]: previousSnapshot,
                },
              }
            : m,
        ),
      );

      setIsStreaming(true);
      const controller = new AbortController();
      abortRef.current = controller;

      let stallTimer: ReturnType<typeof setTimeout> | null = null;
      const armStall = () => {
        if (stallTimer) clearTimeout(stallTimer);
        stallTimer = setTimeout(
          () => controller.abort("stall"),
          STREAM_STALL_TIMEOUT_MS,
        );
      };
      armStall();

      try {
        let res: Response;
        try {
          res = await fetch(
            `/api/chat/messages/${encodeURIComponent(target.server_id)}/regenerate`,
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                [REQUEST_ID_HEADER]: newRequestId(),
              },
              body: JSON.stringify(refinement ? { refinement } : {}),
              signal: controller.signal,
            },
          );
        } catch (err) {
          throw networkError(err);
        }

        if (!res.ok || !res.body) {
          throw await parseApiError(res);
        }

        await consumeSseStream(res.body, (event) => {
          armStall();
          handleEvent(event, assistantLocalId, {
            setMessages,
            setCurrentConvoId,
            setKnowledgeGap,
            onConversationStarted,
          });
        });

        setMessages((prev) =>
          prev.map((m) =>
            m.local_id === assistantLocalId && m.status === "streaming"
              ? { ...m, status: "complete" }
              : m,
          ),
        );

        const finalConvoId = convoIdRef.current;
        if (finalConvoId) onTurnComplete?.(finalConvoId);
        globalMutate("/api/usage/me");
      } catch (err) {
        const abortReason = (controller.signal as { reason?: unknown }).reason;
        const isAbort = (err as Error)?.name === "AbortError";
        const apiErr: ApiError =
          isAbort && abortReason === "stall"
            ? {
                code: "stream_interrupted",
                status: 0,
                message:
                  "The regeneration stalled — the connection probably dropped. Try again.",
              }
            : (err as ApiError);

        // On any failure, swap the prior branch back into the top-level
        // so the user doesn't lose the answer they had on screen.
        setMessages((prev) =>
          prev.map((m) => {
            if (m.local_id !== assistantLocalId) return m;
            const rollback = m.other_branches[previousBranchIndex];
            const nextOther = { ...m.other_branches };
            delete nextOther[previousBranchIndex];
            return {
              ...m,
              status: isAbort && abortReason !== "stall" ? "aborted" : "error",
              server_id: rollback?.server_id ?? m.server_id,
              content: rollback?.content ?? m.content,
              sources: rollback?.sources ?? m.sources,
              feedback: rollback?.feedback ?? m.feedback,
              confidence: rollback?.confidence ?? m.confidence,
              intent: rollback?.intent ?? m.intent,
              competitor_matches:
                rollback?.competitor_matches ?? m.competitor_matches,
              other_branches: nextOther,
              error:
                isAbort && abortReason !== "stall"
                  ? undefined
                  : {
                      code: apiErr.code,
                      message: apiErr.message,
                      request_id: apiErr.request_id,
                      retry_after: apiErr.retry_after,
                    },
            };
          }),
        );
      } finally {
        if (stallTimer) clearTimeout(stallTimer);
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [isStreaming, messages, onConversationStarted, onTurnComplete],
  );

  /**
   * Flip the active branch on an already-regenerated assistant turn.
   * Optimistic; rolls back if the PATCH fails. Stashes the currently active
   * branch into other_branches before swapping in the target.
   */
  const switchBranch = useCallback(
    async (assistantLocalId: string, targetBranchIndex: number): Promise<void> => {
      const target = messages.find((m) => m.local_id === assistantLocalId);
      if (!target || target.role !== "assistant" || !target.server_id) return;
      if (targetBranchIndex === target.active_branch_index) return;
      const incoming = target.other_branches[targetBranchIndex];
      if (!incoming) return;

      const previousIndex = target.active_branch_index;
      const previousSnapshot: InactiveBranch = {
        server_id: target.server_id,
        content: target.content,
        sources: target.sources,
        feedback: target.feedback,
        confidence: target.confidence,
        intent: target.intent,
        competitor_matches: target.competitor_matches,
      };
      const previousServerId = target.server_id;

      setMessages((prev) =>
        prev.map((m) => {
          if (m.local_id !== assistantLocalId) return m;
          const nextOther = { ...m.other_branches };
          delete nextOther[targetBranchIndex];
          nextOther[previousIndex] = previousSnapshot;
          return {
            ...m,
            server_id: incoming.server_id,
            content: incoming.content,
            sources: incoming.sources,
            feedback: incoming.feedback,
            confidence: incoming.confidence,
            intent: incoming.intent,
            competitor_matches: incoming.competitor_matches,
            active_branch_index: targetBranchIndex,
            other_branches: nextOther,
          };
        }),
      );

      try {
        const res = await fetch(
          `/api/chat/messages/${encodeURIComponent(previousServerId)}/active-branch`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ branch_index: targetBranchIndex }),
          },
        );
        if (!res.ok) throw new Error(`active-branch ${res.status}`);
      } catch {
        // Rollback the swap silently — branch position is low-stakes UI state.
        setMessages((prev) =>
          prev.map((m) => {
            if (m.local_id !== assistantLocalId) return m;
            const stashedPrev = m.other_branches[previousIndex];
            if (!stashedPrev) return m;
            const nextOther = { ...m.other_branches };
            delete nextOther[previousIndex];
            nextOther[targetBranchIndex] = {
              server_id: m.server_id,
              content: m.content,
              sources: m.sources,
              feedback: m.feedback,
              confidence: m.confidence,
              intent: m.intent,
              competitor_matches: m.competitor_matches,
            };
            return {
              ...m,
              server_id: stashedPrev.server_id,
              content: stashedPrev.content,
              sources: stashedPrev.sources,
              feedback: stashedPrev.feedback,
              confidence: stashedPrev.confidence,
              intent: stashedPrev.intent,
              competitor_matches: stashedPrev.competitor_matches,
              active_branch_index: previousIndex,
              other_branches: nextOther,
            };
          }),
        );
      }
    },
    [messages],
  );

  /**
   * Optimistic feedback toggle. The bubble flips instantly; the PATCH is
   * fire-and-forget but rolls back on failure so a 500 doesn't leave the UI
   * lying about persisted state. Re-clicking the same thumb clears the vote.
   */
  const setFeedback = useCallback(
    async (assistantLocalId: string, next: MessageFeedback) => {
      let serverId: string | null = null;
      let previous: MessageFeedback | null = null;
      let resolved: MessageFeedback | null = null;

      setMessages((prev) =>
        prev.map((m) => {
          if (m.local_id !== assistantLocalId) return m;
          serverId = m.server_id;
          previous = m.feedback;
          resolved = m.feedback === next ? null : next;
          return { ...m, feedback: resolved };
        }),
      );

      if (!serverId) return; // not yet persisted — can't rate

      try {
        const res = await fetch(
          `/api/chat/messages/${encodeURIComponent(serverId)}/feedback`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ feedback: resolved }),
          },
        );
        if (!res.ok) throw new Error(`feedback ${res.status}`);
      } catch {
        // Roll back. We deliberately don't toast here — the rating is
        // low-stakes telemetry and a failed PATCH is fine to silently revert.
        setMessages((prev) =>
          prev.map((m) =>
            m.local_id === assistantLocalId ? { ...m, feedback: previous } : m,
          ),
        );
      }
    },
    [],
  );

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  return {
    messages,
    isStreaming,
    conversationId: currentConvoId,
    knowledgeGap,
    dismissKnowledgeGap,
    send,
    stop,
    retry,
    setFeedback,
    regenerate,
    switchBranch,
  };
}

// ── helpers ────────────────────────────────────────────────────────────────

function handleEvent(
  event: ChatStreamEvent,
  assistantLocalId: string,
  ctx: {
    setMessages: React.Dispatch<React.SetStateAction<DisplayMessage[]>>;
    setCurrentConvoId: React.Dispatch<React.SetStateAction<string | null>>;
    setKnowledgeGap: React.Dispatch<React.SetStateAction<KnowledgeGap | null>>;
    onConversationStarted?: (id: string) => void;
  },
) {
  const { setMessages, setCurrentConvoId, setKnowledgeGap, onConversationStarted } = ctx;

  switch (event.type) {
    case "start": {
      setCurrentConvoId(event.conversation_id);
      onConversationStarted?.(event.conversation_id);
      // Regenerate start carries parent_user_message_id + branch_index —
      // attach them so the navigator can pick up the right counts even
      // before `done` arrives.
      if (event.parent_user_message_id !== undefined) {
        setMessages((prev) =>
          prev.map((m) =>
            m.local_id === assistantLocalId
              ? {
                  ...m,
                  parent_user_message_id:
                    event.parent_user_message_id ?? m.parent_user_message_id,
                  active_branch_index:
                    event.branch_index ?? m.active_branch_index,
                }
              : m,
          ),
        );
      }
      return;
    }
    case "intent": {
      setMessages((prev) =>
        prev.map((m) =>
          m.local_id === assistantLocalId ? { ...m, intent: event.intent } : m,
        ),
      );
      return;
    }
    case "searching": {
      setMessages((prev) =>
        prev.map((m) =>
          m.local_id === assistantLocalId
            ? {
                ...m,
                searches: [
                  ...m.searches,
                  { query: event.query, status: "running", hit_count: null },
                ],
              }
            : m,
        ),
      );
      return;
    }
    case "searched": {
      setMessages((prev) =>
        prev.map((m) => {
          if (m.local_id !== assistantLocalId) return m;
          const searches = [...m.searches];
          for (let i = searches.length - 1; i >= 0; i--) {
            if (
              searches[i].query === event.query &&
              searches[i].status === "running"
            ) {
              searches[i] = {
                query: event.query,
                status: "done",
                hit_count: event.hit_count,
              };
              break;
            }
          }
          return { ...m, searches };
        }),
      );
      return;
    }
    case "sources": {
      setMessages((prev) =>
        prev.map((m) =>
          m.local_id === assistantLocalId
            ? { ...m, sources: event.sources }
            : m,
        ),
      );
      return;
    }
    case "knowledge_gap": {
      setKnowledgeGap({
        topics: event.topics ?? [],
        detected_at: Date.now(),
      });
      return;
    }
    case "competitor_warning": {
      setMessages((prev) =>
        prev.map((m) =>
          m.local_id === assistantLocalId
            ? { ...m, competitor_matches: event.matches ?? [] }
            : m,
        ),
      );
      return;
    }
    case "confidence": {
      setMessages((prev) =>
        prev.map((m) =>
          m.local_id === assistantLocalId
            ? {
                ...m,
                confidence: {
                  level: event.level,
                  score: event.score,
                  n: event.chunks_considered,
                },
              }
            : m,
        ),
      );
      return;
    }
    case "token": {
      setMessages((prev) =>
        prev.map((m) =>
          m.local_id === assistantLocalId
            ? { ...m, content: m.content + event.text }
            : m,
        ),
      );
      return;
    }
    case "done": {
      setMessages((prev) =>
        prev.map((m) =>
          m.local_id === assistantLocalId
            ? {
                ...m,
                server_id: event.message_id,
                status: "complete",
                active_branch_index:
                  event.branch_index ?? m.active_branch_index,
                total_branches: event.total_branches ?? m.total_branches,
                parent_user_message_id:
                  event.parent_user_message_id ?? m.parent_user_message_id,
              }
            : m,
        ),
      );
      return;
    }
    case "error": {
      // Rich error envelope inside the stream — preserve code/request_id so the
      // bubble can show 429 countdowns and 5xx references.
      const e = event as ChatStreamEvent & {
        code?: ErrorCode;
        request_id?: string;
        retry_after?: number;
      };
      setMessages((prev) =>
        prev.map((m) =>
          m.local_id === assistantLocalId
            ? {
                ...m,
                status: "error",
                error: {
                  code: e.code ?? "upstream_unavailable",
                  message: event.message,
                  request_id: e.request_id,
                  retry_after: e.retry_after,
                },
              }
            : m,
        ),
      );
      return;
    }
    case "moderation_block": {
      // V4 #79 — server emitted a single SSE frame and closed the stream.
      // Settle the assistant bubble straight into the moderation error state
      // so ErrorPanel renders the amber/shield UI (and suppresses retry,
      // since the same input would just re-trip the catalog).
      setMessages((prev) =>
        prev.map((m) =>
          m.local_id === assistantLocalId
            ? {
                ...m,
                status: "error",
                error: {
                  code: "moderation_blocked",
                  message: event.message,
                  request_id: event.request_id,
                },
              }
            : m,
        ),
      );
      return;
    }
  }
}

/**
 * Pull SSE events off a streaming body. We do our own parser instead of
 * EventSource because EventSource is GET-only and can't carry a JSON body.
 */
async function consumeSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      let sep: number;
      while ((sep = findFrameEnd(buf)) !== -1) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep).replace(/^(\r?\n){1,2}/, "");
        const event = parseSseFrame(frame);
        if (event) onEvent(event);
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function findFrameEnd(s: string): number {
  const i1 = s.indexOf("\n\n");
  const i2 = s.indexOf("\r\n\r\n");
  if (i1 === -1) return i2;
  if (i2 === -1) return i1;
  return Math.min(i1, i2);
}

function parseSseFrame(frame: string): ChatStreamEvent | null {
  // Comments (lines beginning with `:`) are SSE keepalives — skip them.
  const dataLines = frame
    .split(/\r?\n/)
    .filter((l) => l.startsWith("data:"))
    .map((l) => l.slice(5).trimStart());
  if (dataLines.length === 0) return null;
  const payload = dataLines.join("\n");
  try {
    return JSON.parse(payload) as ChatStreamEvent;
  } catch {
    return null;
  }
}
