import { apiRequest, apiStream } from "@/services/api-client";
import type {
  ChatStreamEvent,
  Conversation,
  ConversationDetail,
  ProviderInfo,
  TurnMetadata,
} from "@/types/chat";

export async function listConversations(): Promise<Conversation[]> {
  return apiRequest<Conversation[]>("/conversations");
}

export async function createConversation(title = "New research"): Promise<Conversation> {
  return apiRequest<Conversation>("/conversations", { method: "POST", body: { title } });
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  return apiRequest<ConversationDetail>(`/conversations/${id}`);
}

export async function deleteConversation(id: string): Promise<void> {
  return apiRequest<void>(`/conversations/${id}`, { method: "DELETE" });
}

export async function getProviderInfo(): Promise<ProviderInfo> {
  return apiRequest<ProviderInfo>("/llm/provider");
}

/** One parsed `event:`/`data:` frame. */
interface RawFrame {
  event: string;
  data: string;
}

function parseFrame(frame: string): RawFrame | null {
  let event = "";
  const dataLines: string[] = [];

  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7);
    else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }

  if (!event) return null;
  return { event, data: dataLines.join("\n") || "{}" };
}

function toEvent(frame: RawFrame): ChatStreamEvent | null {
  const payload = JSON.parse(frame.data) as Record<string, unknown>;

  switch (frame.event) {
    case "user_message":
      return {
        type: "user_message",
        message_id: String(payload["message_id"]),
        conversation_id: String(payload["conversation_id"]),
      };
    case "delta":
      return { type: "delta", text: String(payload["text"]) };
    case "done":
      return { type: "done", metadata: payload as unknown as TurnMetadata };
    case "error":
      return {
        type: "error",
        code: String(payload["code"]),
        message: String(payload["message"]),
        partial: Boolean(payload["partial"]),
      };
    default:
      // Unknown event names are ignored rather than thrown on, so adding a new
      // server-side event does not break older clients.
      return null;
  }
}

/**
 * Stream one chat turn.
 *
 * Frames are separated by a blank line, and a chunk boundary can land anywhere —
 * including mid-frame — so the tail is buffered until its terminator arrives.
 */
export async function* streamMessage(
  conversationId: string,
  content: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const response = await apiStream(`/conversations/${conversationId}/messages`, {
    method: "POST",
    body: { content },
    ...(signal ? { signal } : {}),
  });

  if (response.body === null) throw new Error("The server returned an empty stream.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      let separator = buffer.indexOf("\n\n");
      while (separator !== -1) {
        const frame = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);

        const parsed = parseFrame(frame);
        if (parsed !== null) {
          const event = toEvent(parsed);
          if (event !== null) yield event;
        }
        separator = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}
