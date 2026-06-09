"use client";

import { use, useCallback, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { ConversationSidebar } from "@/components/chat/conversation-sidebar";
import { MessageInput } from "@/components/chat/message-input";
import { MessageList } from "@/components/chat/message-list";
import { useChat } from "@/hooks/use-chat";
import { useConversation } from "@/hooks/use-conversation";
import { useConversations } from "@/hooks/use-conversations";
import { useDocuments } from "@/hooks/use-documents";

export default function ChatConversationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { documents, loading: loadingDocs } = useDocuments();
  const { messages: persistedMessages, loading, error } = useConversation(id);
  const { touch, refresh } = useConversations();

  // Shown only to seed useChat — once the user sends a message, the hook owns
  // the timeline. Memoize so we don't reset state on every render.
  const initialMessages = useMemo(
    () =>
      persistedMessages.map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        sources: m.sources,
        created_at: m.created_at,
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

  const { messages, isStreaming, send, stop, retry } = useChat({
    conversationId: id,
    initialMessages,
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

  const hasDocuments = !loadingDocs && documents.length > 0;
  const noContent = !loading && messages.length === 0;

  return (
    <>
      <ConversationSidebar activeId={id} />
      <main className="flex h-full min-h-0 flex-1 flex-col">
        {loading && messages.length === 0 ? (
          <div className="flex flex-1 items-center justify-center">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        ) : noContent ? (
          <div className="flex flex-1 items-center justify-center px-6 py-12 text-center">
            <p className="max-w-sm text-sm text-muted-foreground">
              This conversation has no messages yet. Send something to get
              started.
            </p>
          </div>
        ) : (
          <MessageList messages={messages} onRetry={retry} />
        )}

        <MessageInput
          onSend={send}
          onStop={stop}
          isStreaming={isStreaming}
          disabled={!hasDocuments && noContent}
          disabledReason={
            !hasDocuments && noContent
              ? "Upload a document first so the AI has context to work with."
              : undefined
          }
        />
      </main>
    </>
  );
}
