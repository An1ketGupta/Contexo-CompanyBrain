"use client";

import { use, useCallback, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ArchivedBanner } from "@/components/chat/archived-banner";
import { ChatMobileBar } from "@/components/chat/chat-mobile-bar";
import { ConversationSidebar } from "@/components/chat/conversation-sidebar";
import { DocumentStatusBanner } from "@/components/chat/document-status-banner";
import { ExportButton } from "@/components/chat/export-button";
import { KnowledgeGapBanner } from "@/components/chat/knowledge-gap-banner";
import { MessageInput } from "@/components/chat/message-input";
import { MessageList } from "@/components/chat/message-list";
import { ChatMessagesSkeleton } from "@/components/chat/chat-messages-skeleton";
import { PinContextButton } from "@/components/chat/pin-context-button";
import { ScopeBanner } from "@/components/chat/scope-banner";
import { useChat } from "@/hooks/use-chat";
import { useConversation } from "@/hooks/use-conversation";
import { useConversations } from "@/hooks/use-conversations";
import { useDocumentStatus } from "@/hooks/use-document-status";
import { useDocuments } from "@/hooks/use-documents";
import {
  copyToClipboardWithToast,
  useKeyboardShortcuts,
} from "@/hooks/use-keyboard-shortcuts";

export default function ChatConversationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { documents, loading: loadingDocs } = useDocuments();
  const {
    conversation,
    messages: persistedMessages,
    branches: persistedBranches,
    loading,
    error,
    refresh: refreshConversation,
  } = useConversation(id);
  const { touch, refresh } = useConversations();

  const scopedDocument = useMemo(() => {
    const scopeId = conversation?.scoped_document_id;
    if (!scopeId) return null;
    return documents.find((d) => d.id === scopeId) ?? null;
  }, [conversation?.scoped_document_id, documents]);

  // Shown only to seed useChat — once the user sends a message, the hook owns
  // the timeline. Memoize so we don't reset state on every render.
  const initialMessages = useMemo(
    () =>
      persistedMessages.map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        sources: m.sources,
        feedback: m.feedback ?? null,
        confidence: m.metadata?.confidence ?? m.confidence ?? null,
        intent: m.metadata?.intent ?? null,
        created_at: m.created_at,
        parent_user_message_id: m.parent_user_message_id ?? null,
        branch_index: m.branch_index ?? 0,
        total_branches: m.total_branches ?? 1,
      })),
    [persistedMessages],
  );

  const handleTurnComplete = useCallback(
    (convoId: string) => {
      touch(convoId);
      refresh();
    },
    [touch, refresh],
  );

  const {
    messages,
    isStreaming,
    knowledgeGap,
    dismissKnowledgeGap,
    send,
    stop,
    retry,
    setFeedback,
    regenerate,
    switchBranch,
  } = useChat({
    conversationId: id,
    initialMessages,
    initialBranches: persistedBranches,
    onTurnComplete: handleTurnComplete,
  });

  // If the conversation doesn't exist (deleted from another tab, e.g.),
  // bounce to /chat so the user isn't stuck staring at an error.
  useEffect(() => {
    if (error) {
      toast.error(error);
      router.replace("/chat");
    }
  }, [error, router]);

  // V4 #39 — auto-send a prefilled prompt if the previous route (e.g. the
  // meeting-prep form) stashed one in sessionStorage. The key is keyed by
  // conversation id so a stale prefill from another conversation never fires
  // here, and we delete it on use to enforce one-shot semantics. We wait for
  // the conversation to load AND for messages to actually be empty — sending
  // again on a thread that already has turns would just be confusing.
  useEffect(() => {
    if (loading || !conversation || messages.length > 0 || isStreaming) return;
    if (typeof window === "undefined") return;
    const key = `chat-prefill:${id}`;
    const stashed = window.sessionStorage.getItem(key);
    if (!stashed) return;
    window.sessionStorage.removeItem(key);
    void send(stashed);
    // We don't list `send` in deps — useChat returns a stable function but
    // listing it would risk re-firing if the hook ever changes identity. The
    // `messages.length > 0` guard above prevents a double-send regardless.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, conversation, id, isStreaming]);

  // Deep-link support: `/chat/{id}#m-{message_id}` (used by the admin
  // /admin/feedback page and the threshold-alert email). We wait for the
  // messages list to render — otherwise the target element doesn't exist
  // yet and the browser's native :target scroll runs against nothing. The
  // MessageList's stick-to-bottom effect will still fire on initial mount;
  // our scroll runs afterwards so the highlighted message wins.
  useEffect(() => {
    if (loading || messages.length === 0) return;
    if (typeof window === "undefined") return;
    const hash = window.location.hash;
    if (!hash.startsWith("#m-")) return;
    const el = document.getElementById(hash.slice(1));
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    // Brief ring flash so the admin can see which row the email pointed at.
    el.classList.add("ring-2", "ring-amber-400/70", "rounded-md");
    const timer = window.setTimeout(() => {
      el.classList.remove("ring-2", "ring-amber-400/70", "rounded-md");
    }, 2400);
    return () => window.clearTimeout(timer);
  }, [loading, messages.length]);

  useKeyboardShortcuts({
    isStreaming,
    onStopGeneration: stop,
    onCopyLastResponse: () => {
      const last = [...messages].reverse().find((m) => m.role === "assistant");
      void copyToClipboardWithToast(last?.content ?? null);
    },
  });

  const hasDocuments = !loadingDocs && documents.length > 0;
  const noContent = !loading && messages.length === 0;
  const { status: docStatus } = useDocumentStatus();
  // Disable input whenever no document has reached `ready` yet — without one,
  // any answer the LLM produces is hallucinated. The doc-status banner above
  // already explains the situation.
  const noReadyDocs = !!docStatus && !docStatus.has_ready;

  return (
    <>
      <ConversationSidebar activeId={id} />
      <main className="flex h-full min-h-0 flex-1 flex-col">
        <ChatMobileBar
          activeId={id}
          trailing={
            conversation && messages.length > 0 ? (
              <ExportButton
                conversationId={id}
                title={conversation.title ?? "Conversation"}
              />
            ) : null
          }
        />
        {messages.length > 0 && (
          <div className="hidden items-center justify-between gap-2 border-b border-border bg-background px-6 py-2.5 md:flex">
            <div className="min-w-0 flex-1">
              <h1 className="truncate text-sm font-semibold tracking-tight text-foreground">
                {conversation?.title ?? "Conversation"}
              </h1>
            </div>
            <PinContextButton
              conversationId={id}
              initialValue={conversation?.pinned_context ?? null}
              onSaved={() => refreshConversation()}
            />
            <ExportButton
              conversationId={id}
              title={conversation?.title ?? "Conversation"}
            />
          </div>
        )}
        {/* V3 #104 — surface archive state above other banners so the user
            sees it before reading. The chat input stays enabled; sending a
            message auto-restores via the backend. */}
        {conversation?.is_archived && (
          <ArchivedBanner
            conversationId={id}
            archivedAt={conversation.archived_at}
            archiveReason={conversation.archive_reason}
            onRestored={() => {
              refreshConversation();
              refresh();
            }}
          />
        )}
        {(scopedDocument || conversation?.scoped_document_id) && (
          <ScopeBanner
            documentName={
              scopedDocument?.name ?? "(deleted document)"
            }
          />
        )}
        {conversation?.scoped_tags && conversation.scoped_tags.length > 0 && (
          <ScopeBanner tags={conversation.scoped_tags} />
        )}
        <DocumentStatusBanner />
        {loading && messages.length === 0 ? (
          <ChatMessagesSkeleton />
        ) : noContent ? (
          <div className="flex flex-1 items-center justify-center px-6 py-12 text-center">
            <p className="max-w-sm text-sm text-muted-foreground">
              This conversation has no messages yet. Send something to get
              started.
            </p>
          </div>
        ) : (
          <MessageList
            messages={messages}
            onRetry={retry}
            onFeedback={setFeedback}
            onRegenerate={regenerate}
            onSwitchBranch={switchBranch}
            isStreaming={isStreaming}
          />
        )}

        <div className="px-4 md:px-6">
          <KnowledgeGapBanner gap={knowledgeGap} onDismiss={dismissKnowledgeGap} />
        </div>

        <MessageInput
          onSend={send}
          onStop={stop}
          isStreaming={isStreaming}
          disabled={noReadyDocs}
          disabledReason={
            noReadyDocs
              ? docStatus?.total === 0
                ? "Upload a document first so the AI has context to work with."
                : "Documents are still processing — chat unlocks once at least one is ready."
              : undefined
          }
          placeholder={
            noReadyDocs ? "Upload documents to start asking…" : undefined
          }
        />
      </main>
    </>
  );
}
