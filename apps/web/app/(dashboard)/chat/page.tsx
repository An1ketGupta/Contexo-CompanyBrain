"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ConversationSidebar } from "@/components/chat/conversation-sidebar";
import { EmptyState } from "@/components/chat/empty-state";
import { MessageInput } from "@/components/chat/message-input";
import { MessageList } from "@/components/chat/message-list";
import { useChat } from "@/hooks/use-chat";
import { useConversations } from "@/hooks/use-conversations";
import { useDocuments } from "@/hooks/use-documents";

/**
 * `/chat` — start a new conversation. The send() call carries no
 * conversation_id; the backend mints one and emits it via the `start` event.
 * When we receive it we navigate to /chat/[id] so the URL becomes shareable
 * and reloadable without losing the thread.
 */
export default function ChatNewPage() {
  const router = useRouter();
  const { documents, loading: loadingDocs } = useDocuments();
  const { prepend, touch, refresh } = useConversations();

  const [pendingNavId, setPendingNavId] = useState<string | null>(null);

  const handleConversationStarted = useCallback(
    (id: string) => {
      // We optimistically place a placeholder so the sidebar updates instantly.
      // The real title settles once the backend persists it.
      prepend(id, "New conversation");
      setPendingNavId(id);
    },
    [prepend],
  );

  const handleTurnComplete = useCallback(
    (id: string) => {
      touch(id);
      // Refresh once so the title (derived from first message) appears.
      refresh();
    },
    [touch, refresh],
  );

  const { messages, isStreaming, send, stop, retry, setFeedback } = useChat({
    conversationId: null,
    onConversationStarted: handleConversationStarted,
    onTurnComplete: handleTurnComplete,
  });

  // After the first turn finishes (so the user sees the stream play out),
  // route to the canonical /chat/[id] URL.
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
        {isEmpty ? (
          <div className="flex-1 overflow-y-auto">
            <EmptyState onPick={send} hasDocuments={hasDocuments} />
          </div>
        ) : (
          <MessageList messages={messages} onRetry={retry} onFeedback={setFeedback} />
        )}
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
