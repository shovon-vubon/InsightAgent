"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useChat } from "@/features/chat/use-chat";
import * as chatService from "@/services/chat";
import type { ChatMessage } from "@/types/chat";

function TestDoubleBanner() {
  return (
    <div className="border-b border-amber-200 bg-amber-50 px-6 py-2.5 text-xs text-amber-900">
      <strong className="font-semibold">Deterministic test provider.</strong> No language model is
      being called. Set <code className="font-mono">LLM_PROVIDER</code> and the matching API key in{" "}
      <code className="font-mono">.env</code> for real responses.
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "USER";
  return (
    <div className={isUser ? "flex justify-end" : "flex justify-start"}>
      <div
        className={[
          "max-w-2xl rounded-lg px-4 py-2.5 text-sm whitespace-pre-wrap",
          isUser
            ? "bg-[--color-accent] text-white"
            : "border border-[--color-border] bg-white text-[--color-ink]",
        ].join(" ")}
      >
        {message.content}
      </div>
    </div>
  );
}

export function ChatWorkspace() {
  const queryClient = useQueryClient();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const { data: provider } = useQuery({
    queryKey: ["provider"],
    queryFn: chatService.getProviderInfo,
  });

  const { data: conversations = [] } = useQuery({
    queryKey: ["conversations"],
    queryFn: chatService.listConversations,
  });

  const chat = useChat(conversationId);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.messages.length, chat.streaming]);

  async function startConversation() {
    const conversation = await chatService.createConversation();
    setConversationId(conversation.id);
    chat.reset();
    await queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }

  async function openConversation(id: string) {
    setConversationId(id);
    await chat.load(id);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const content = draft.trim();
    if (!content) return;

    let target = conversationId;
    if (target === null) {
      const conversation = await chatService.createConversation();
      target = conversation.id;
      setConversationId(target);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    }

    setDraft("");
    await chat.send(content);
    await queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }

  return (
    <div className="flex h-screen flex-col">
      {provider?.is_test_double ? <TestDoubleBanner /> : null}

      <div className="flex min-h-0 flex-1">
        <div className="flex w-64 shrink-0 flex-col border-r border-[--color-border] bg-white">
          <div className="p-3">
            <Button className="w-full" onClick={() => void startConversation()}>
              New research
            </Button>
          </div>
          <nav className="flex-1 overflow-y-auto px-2 pb-3" aria-label="Conversations">
            {conversations.map((conversation) => (
              <button
                key={conversation.id}
                onClick={() => void openConversation(conversation.id)}
                aria-current={conversation.id === conversationId ? "true" : undefined}
                className={[
                  "mb-1 w-full truncate rounded-md px-3 py-2 text-left text-sm",
                  conversation.id === conversationId
                    ? "bg-[--color-surface-muted] font-medium"
                    : "hover:bg-[--color-surface-muted]",
                ].join(" ")}
              >
                {conversation.title}
              </button>
            ))}
          </nav>
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 overflow-y-auto px-6 py-6">
            <div className="mx-auto flex max-w-3xl flex-col gap-4">
              {chat.messages.length === 0 && chat.streaming === null ? (
                <div className="mt-16 text-center">
                  <h1 className="text-lg font-semibold">Ask a research question</h1>
                  <p className="mt-2 text-sm text-[--color-ink-muted]">
                    Document retrieval, SQL, and analysis tools arrive in later phases. Right now
                    this is conversation only.
                  </p>
                </div>
              ) : null}

              {chat.messages.map((message) => (
                <MessageBubble key={message.id} message={message} />
              ))}

              {chat.streaming !== null ? (
                <div className="flex justify-start">
                  <div className="max-w-2xl rounded-lg border border-[--color-border] bg-white px-4 py-2.5 text-sm whitespace-pre-wrap">
                    {chat.streaming}
                    <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-[--color-ink-muted] align-middle" />
                  </div>
                </div>
              ) : null}

              {chat.error !== null ? (
                <p
                  role="alert"
                  className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-[--color-danger]"
                >
                  {chat.error}
                </p>
              ) : null}

              {chat.metadata !== null ? (
                <p className="text-xs text-[--color-ink-muted]">
                  {chat.metadata.provider}/{chat.metadata.model} · {chat.metadata.input_tokens} in
                  {" / "}
                  {chat.metadata.output_tokens} out · {Math.round(chat.metadata.latency_ms)} ms
                  {chat.metadata.cost_usd !== null
                    ? ` · $${Number(chat.metadata.cost_usd).toFixed(6)}`
                    : " · cost unknown for this model"}
                </p>
              ) : null}

              <div ref={bottomRef} />
            </div>
          </div>

          <form
            onSubmit={(event) => void handleSubmit(event)}
            className="border-t border-[--color-border] bg-white px-6 py-4"
          >
            <div className="mx-auto flex max-w-3xl gap-3">
              <label htmlFor="chat-input" className="sr-only">
                Your question
              </label>
              <textarea
                id="chat-input"
                rows={2}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void handleSubmit(event);
                  }
                }}
                placeholder="Ask a question…  (Enter to send, Shift+Enter for a new line)"
                className="flex-1 resize-none rounded-md border border-[--color-border] px-3 py-2 text-sm"
              />
              {chat.isSending ? (
                <Button type="button" variant="secondary" onClick={chat.cancel}>
                  Stop
                </Button>
              ) : (
                <Button type="submit" disabled={!draft.trim()}>
                  Send
                </Button>
              )}
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
