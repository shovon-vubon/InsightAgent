"use client";

import { useCallback, useRef, useState } from "react";

import * as chatService from "@/services/chat";
import type { ChatMessage, TurnMetadata } from "@/types/chat";

interface ChatState {
  messages: ChatMessage[];
  /** Text accumulated for the reply currently arriving, if any. */
  streaming: string | null;
  metadata: TurnMetadata | null;
  error: string | null;
  isSending: boolean;
}

const EMPTY: ChatState = {
  messages: [],
  streaming: null,
  metadata: null,
  error: null,
  isSending: false,
};

export function useChat(conversationId: string | null) {
  const [state, setState] = useState<ChatState>(EMPTY);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async (id: string) => {
    const conversation = await chatService.getConversation(id);
    setState({ ...EMPTY, messages: conversation.messages });
  }, []);

  const reset = useCallback(() => setState(EMPTY), []);

  const send = useCallback(
    async (content: string) => {
      if (conversationId === null || !content.trim()) return;

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // Optimistic echo so the input clears immediately; the id is replaced when
      // the server confirms it.
      const optimistic: ChatMessage = {
        id: `pending-${Date.now()}`,
        sequence: Number.MAX_SAFE_INTEGER,
        role: "USER",
        content,
        created_at: new Date().toISOString(),
      };

      setState((previous) => ({
        ...previous,
        messages: [...previous.messages, optimistic],
        streaming: "",
        metadata: null,
        error: null,
        isSending: true,
      }));

      let accumulated = "";
      try {
        for await (const event of chatService.streamMessage(
          conversationId,
          content,
          controller.signal,
        )) {
          switch (event.type) {
            case "user_message":
              setState((previous) => ({
                ...previous,
                messages: previous.messages.map((message) =>
                  message.id === optimistic.id ? { ...message, id: event.message_id } : message,
                ),
              }));
              break;

            case "delta":
              accumulated += event.text;
              setState((previous) => ({ ...previous, streaming: accumulated }));
              break;

            case "done":
              setState((previous) => ({
                ...previous,
                streaming: null,
                metadata: event.metadata,
                isSending: false,
                messages: [
                  ...previous.messages,
                  {
                    id: event.metadata.message_id,
                    sequence: previous.messages.length + 1,
                    role: "ASSISTANT",
                    content: accumulated,
                    created_at: new Date().toISOString(),
                  },
                ],
              }));
              break;

            case "error":
              // Whatever already arrived is kept: the user watched it appear, and
              // discarding it would look like the system lost work.
              setState((previous) => ({
                ...previous,
                streaming: null,
                isSending: false,
                error: event.message,
                messages: accumulated
                  ? [
                      ...previous.messages,
                      {
                        id: `partial-${Date.now()}`,
                        sequence: previous.messages.length + 1,
                        role: "ASSISTANT",
                        content: accumulated,
                        created_at: new Date().toISOString(),
                      },
                    ]
                  : previous.messages,
              }));
              break;
          }
        }
      } catch (caught) {
        if (controller.signal.aborted) return;
        setState((previous) => ({
          ...previous,
          streaming: null,
          isSending: false,
          error: caught instanceof Error ? caught.message : "The request failed.",
        }));
      }
    },
    [conversationId],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setState((previous) => ({ ...previous, isSending: false, streaming: null }));
  }, []);

  return { ...state, send, load, reset, cancel };
}
