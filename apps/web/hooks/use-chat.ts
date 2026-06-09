"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { networkError, parseApiError, type ApiError, type ErrorCode } from "@/lib/errors";
import { newRequestId, REQUEST_ID_HEADER } from "@/lib/request-id";
import type { ChatStreamEvent, MessageFeedback, MessageSource } from "@/lib/types";

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
}

export interface UseChatOptions {
  conversationId: string | null;
  initialMessages?: Array<{
    id: string;
    role: DisplayRole;
    content: string;
    sources: MessageSource[] | null;
    feedback?: MessageFeedback | null;
    created_at: string;
  }>;
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
): DisplayMessage {
  return {
    local_id: m.id,
    server_id: m.id,
    role: m.role,
    content: m.content,
    sources: m.sources ?? [],
    searches: [],
    status: "complete",
    feedback: m.feedback ?? null,
    created_at: m.created_at,
  };
}

export function useChat({
  conversationId,
  initialMessages,
  onConversationStarted,
  onTurnComplete,
}: UseChatOptions) {
  const [messages, setMessages] = useState<DisplayMessage[]>(() =>
    (initialMessages ?? []).map(persistedToDisplay),
  );
  const [isStreaming, setIsStreaming] = useState(false);
  const [currentConvoId, setCurrentConvoId] = useState<string | null>(
    conversationId,
  );

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
      setMessages((initialMessages ?? []).map(persistedToDisplay));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  useEffect(() => {
    if (!initialMessages || initialMessages.length === 0) return;
    setMessages((prev) => {
      if (prev.length > 0) return prev;
      return initialMessages.map(persistedToDisplay);
    });
  }, [initialMessages]);

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
          pending_text: trimmed,
          client_message_id: clientMessageId,
          created_at: new Date().toISOString(),
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
          pending_text: trimmed,
          client_message_id: clientMessageId,
          created_at: new Date().toISOString(),
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
    [isStreaming, onConversationStarted, onTurnComplete],
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
    send,
    stop,
    retry,
    setFeedback,
  };
}

// ── helpers ────────────────────────────────────────────────────────────────

function handleEvent(
  event: ChatStreamEvent,
  assistantLocalId: string,
  ctx: {
    setMessages: React.Dispatch<React.SetStateAction<DisplayMessage[]>>;
    setCurrentConvoId: React.Dispatch<React.SetStateAction<string | null>>;
    onConversationStarted?: (id: string) => void;
  },
) {
  const { setMessages, setCurrentConvoId, onConversationStarted } = ctx;

  switch (event.type) {
    case "start": {
      setCurrentConvoId(event.conversation_id);
      onConversationStarted?.(event.conversation_id);
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
            ? { ...m, server_id: event.message_id, status: "complete" }
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
