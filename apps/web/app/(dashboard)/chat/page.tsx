"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ConversationSidebar } from "@/components/chat/conversation-sidebar";
import { EmptyState } from "@/components/chat/empty-state";
import { KnowledgeGapBanner } from "@/components/chat/knowledge-gap-banner";
import { MessageInput } from "@/components/chat/message-input";
import { MessageList } from "@/components/chat/message-list";
import { ScopeBanner } from "@/components/chat/scope-banner";
import { useChat } from "@/hooks/use-chat";
import { useConversations } from "@/hooks/use-conversations";
import { useDocuments } from "@/hooks/use-documents";

/**
 * `/chat` — start a new conversation. The send() call carries no
 * conversation_id; the backend mints one and emits it via the `start` event.
 * When we receive it we navigate to /chat/[id] so the URL becomes shareable
 * and reloadable without losing the thread.
 *
 * `?document_id=…` pins the new conversation to a single document. We pass
 * the scope hint into useChat and trust the backend to persist it on the
 * first POST. After that the URL is just /chat/[id] and the scope lives on
 * the conversation row.
 */
export default function ChatNewPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const scopedDocumentId = searchParams.get("document_id");
  const { documents, loading: loadingDocs } = useDocuments();
  const { prepend, touch, refresh } = useConversations();

  const [pendingNavId, setPendingNavId] = useState<string | null>(null);

  const scopedDocument = useMemo(() => {
    if (!scopedDocumentId) return null;
    return documents.find((d) => d.id === scopedDocumentId) ?? null;
  }, [documents, scopedDocumentId]);

  const handleConversationStarted = useCallback(
    (id: string) => {
      prepend(id, "New conversation");
      setPendingNavId(id);
    },
    [prepend],
  );

  const handleTurnComplete = useCallback(
    (id: string) => {
      touch(id);
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
  } = useChat({
    conversationId: null,
    scopedDocumentId,
    onConversationStarted: handleConversationStarted,
    onTurnComplete: handleTurnComplete,
  });

  useEffect(() => {
    if (pendingNavId && !isStreaming) {
      router.replace(`/chat/${pendingNavId}`);
    }
  }, [pendingNavId, isStreaming, router]);

  const isEmpty = messages.length === 0;
  const hasDocuments = !loadingDocs && documents.length > 0;

  return (
    <>
      <ConversationSidebar activeId={null} />
      <main className="flex h-full min-h-0 flex-1 flex-col">
        {scopedDocument && (
          <ScopeBanner documentName={scopedDocument.name} clearHref="/chat" />
        )}
        {isEmpty ? (
          <div className="flex-1 overflow-y-auto">
            <EmptyState onPick={send} hasDocuments={hasDocuments} />
          </div>
        ) : (
          <MessageList messages={messages} onRetry={retry} onFeedback={setFeedback} />
        )}
        <div className="px-4 md:px-6">
          <KnowledgeGapBanner gap={knowledgeGap} onDismiss={dismissKnowledgeGap} />
        </div>
        <MessageInput
          onSend={send}
          onStop={stop}
          isStreaming={isStreaming}
          disabled={!hasDocuments && isEmpty}
          disabledReason={
            !hasDocuments && isEmpty
              ? "Upload a document first so the AI has context to work with."
              : undefined
          }
        />
      </main>
    </>
  );
}
