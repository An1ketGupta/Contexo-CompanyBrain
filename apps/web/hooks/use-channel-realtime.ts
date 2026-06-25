"use client";

import { useEffect, useRef } from "react";
import { createClient } from "@/lib/supabase/client";

/**
 * Subscribe to multiplayer chat broadcasts on `conversation:{id}`.
 *
 * The backend (apps/api/app/services/realtime_broadcast.py) re-emits every
 * SSE token/source/done frame from the AI orchestrator onto a Supabase
 * Realtime broadcast topic. This hook subscribes once per conversation
 * and feeds each broadcast event into the supplied handler.
 *
 * Why broadcast vs postgres_changes:
 *   See the rationale in realtime_broadcast.py — token-level postgres rows
 *   would burn IO; broadcast is in-memory and ephemeral.
 *
 * Presence:
 *   We also enable Supabase Realtime presence so each subscriber's user_id
 *   shows up as "online" to the others. Presence sync events fire when the
 *   roster changes; the caller can pass `onPresence` to render avatar
 *   stacks.
 */
export interface RealtimeEvent {
  type: string;
  [key: string]: unknown;
}

export interface PresenceState {
  [userId: string]: { online_at: string; user_id: string };
}

export function useChannelRealtime(opts: {
  conversationId: string | null;
  userId: string | null;
  onEvent: (event: RealtimeEvent) => void;
  onPresence?: (state: PresenceState) => void;
}) {
  const { conversationId, userId, onEvent, onPresence } = opts;
  const onEventRef = useRef(onEvent);
  const onPresenceRef = useRef(onPresence);
  useEffect(() => {
    onEventRef.current = onEvent;
    onPresenceRef.current = onPresence;
  }, [onEvent, onPresence]);

  useEffect(() => {
    if (!conversationId || !userId) return;
    const supabase = createClient();
    const channel = supabase.channel(`conversation:${conversationId}`, {
      config: {
        broadcast: { self: false },
        presence: { key: userId },
      },
    });

    // Catch every broadcast event regardless of name — the backend sends
    // many distinct event names (token, sources, user_message, done, etc.).
    // We forward all of them; consumers filter on payload.type.
    const events = [
      "token",
      "sources",
      "searching",
      "searched",
      "user_message",
      "start",
      "done",
      "error",
      "confidence",
      "intent",
      "moderation_block",
    ];
    for (const ev of events) {
      channel.on("broadcast", { event: ev }, ({ payload }) => {
        onEventRef.current(payload as RealtimeEvent);
      });
    }

    channel.on("presence", { event: "sync" }, () => {
      const state = channel.presenceState() as unknown as PresenceState;
      if (onPresenceRef.current) onPresenceRef.current(state);
    });

    channel.subscribe(async (status) => {
      if (status === "SUBSCRIBED") {
        await channel.track({
          user_id: userId,
          online_at: new Date().toISOString(),
        });
      }
    });

    return () => {
      void channel.unsubscribe();
      void supabase.removeChannel(channel);
    };
  }, [conversationId, userId]);
}
