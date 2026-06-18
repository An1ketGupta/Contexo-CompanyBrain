"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChatMobileBar } from "@/components/chat/chat-mobile-bar";
import { ConversationSidebar } from "@/components/chat/conversation-sidebar";
import { DocumentStatusBanner } from "@/components/chat/document-status-banner";
import { EmptyState } from "@/components/chat/empty-state";
import { KnowledgeGapBanner } from "@/components/chat/knowledge-gap-banner";
import { MessageInput } from "@/components/chat/message-input";
import { MessageList } from "@/components/chat/message-list";
import { ScopeBanner } from "@/components/chat/scope-banner";
import { ScopePopover, type ScopeValue } from "@/components/chat/scope-popover";
import { useChat } from "@/hooks/use-chat";
import { useConversations } from "@/hooks/use-conversations";
import { useDocumentStatus } from "@/hooks/use-document-status";
import { useDocuments } from "@/hooks/use-documents";
import {
  copyToClipboardWithToast,
  useKeyboardShortcuts,
} from "@/hooks/use-keyboard-shortcuts";

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
  const [scope, setScope] = useState<ScopeValue>({
    collectionId: null,
    tags: [],
  });

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
    scopedTags: scopedDocumentId ? [] : scope.tags,
    scopedCollectionId: scopedDocumentId ? null : scope.collectionId,
    onConversationStarted: handleConversationStarted,
    onTurnComplete: handleTurnComplete,
  });

  useEffect(() => {
    if (pendingNavId && !isStreaming) {
      router.replace(`/chat/${pendingNavId}`);
    }
  }, [pendingNavId, isStreaming, router]);

  useKeyboardShortcuts({
    isStreaming,
    onStopGeneration: stop,
    onCopyLastResponse: () => {
      const last = [...messages].reverse().find((m) => m.role === "assistant");
      void copyToClipboardWithToast(last?.content ?? null);
    },
  });

  const isEmpty = messages.length === 0;
  const hasDocuments = !loadingDocs && documents.length > 0;
  const { status: docStatus } = useDocumentStatus();
  const noReadyDocs = !!docStatus && !docStatus.has_ready;

  return (
    <>
      <ConversationSidebar activeId={null} />
      <main className="flex h-full min-h-0 flex-1 flex-col">
        <ChatMobileBar activeId={null} />
        {scopedDocument && (
          <ScopeBanner documentName={scopedDocument.name} clearHref="/chat" />
        )}
        <DocumentStatusBanner />
        {isEmpty && !scopedDocument && (
          <ScopePopover value={scope} onChange={setScope} />
        )}
        {isEmpty ? (
          <div className="flex flex-1 overflow-y-auto">
            <EmptyState hasDocuments={hasDocuments} />
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
