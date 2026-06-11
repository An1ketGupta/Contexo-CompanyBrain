"use client";

import useSWR from "swr";
import type {
  MessageConfidence,
  MessageFeedback,
  MessageMetadata,
  MessageSource,
} from "@/lib/types";

export interface PersistedMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources: MessageSource[] | null;
  feedback?: MessageFeedback | null;
  metadata?: MessageMetadata | null;
  confidence?: MessageConfidence | null;
  created_at: string;
  parent_user_message_id?: string | null;
  branch_index?: number;
  is_active_branch?: boolean;
  total_branches?: number;
}

export interface PersistedBranch {
  id: string;
  branch_index: number;
  is_active_branch: boolean;
  content: string;
  sources: MessageSource[] | null;
  feedback: MessageFeedback | null;
  metadata: MessageMetadata | null;
  created_at: string;
}

interface ConversationResponse {
  conversation: {
    id: string;
    title: string | null;
    is_pinned?: boolean;
    created_at: string;
    updated_at: string;
    scoped_document_id?: string | null;
    scoped_tags?: string[] | null;
  };
  messages: PersistedMessage[];
  branches?: Record<string, PersistedBranch[]>;
}

const fetcher = async (url: string): Promise<ConversationResponse> => {
  const res = await fetch(url);
  if (res.status === 404) {
    throw new Error("Conversation not found.");
  }
  if (!res.ok) throw new Error(`Failed to load conversation (${res.status})`);
  return res.json();
};

export function useConversation(id: string | null) {
  const { data, error, isLoading, mutate } = useSWR<ConversationResponse>(
    id ? `/api/chat/conversations/${id}` : null,
    fetcher,
    { revalidateOnFocus: false },
  );

  return {
    conversation: data?.conversation ?? null,
    messages: data?.messages ?? [],
    branches: data?.branches ?? {},
    loading: isLoading,
    error: error ? (error as Error).message : null,
    refresh: mutate,
  };
}
