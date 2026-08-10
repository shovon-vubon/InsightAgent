export type MessageRole = "USER" | "ASSISTANT" | "SYSTEM";

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  sequence: number;
  role: MessageRole;
  content: string;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
}

export interface ProviderInfo {
  provider: string;
  model: string;
  is_test_double: boolean;
}

/** Accounting for one completed turn, shown under the assistant's reply. */
export interface TurnMetadata {
  message_id: string;
  provider: string;
  model: string;
  is_test_double: boolean;
  finish_reason: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  cost_usd: string | null;
}

export type ChatStreamEvent =
  | { type: "user_message"; message_id: string; conversation_id: string }
  | { type: "delta"; text: string }
  | { type: "done"; metadata: TurnMetadata }
  | { type: "error"; code: string; message: string; partial?: boolean };
