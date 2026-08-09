import { beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken } from "@/services/api-client";
import { streamMessage } from "@/services/chat";
import type { ChatStreamEvent } from "@/types/chat";

/** Build a Response whose body streams the given chunks verbatim. */
function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

async function collect(): Promise<ChatStreamEvent[]> {
  const events: ChatStreamEvent[] = [];
  for await (const event of streamMessage("conversation-1", "why did revenue fall")) {
    events.push(event);
  }
  return events;
}

describe("streamMessage", () => {
  beforeEach(() => {
    setAccessToken("token-abc");
  });

  it("parses a well-formed stream into typed events", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: user_message\ndata: {"message_id":"m1","conversation_id":"c1"}\n\n',
          'event: delta\ndata: {"text":"Revenue "}\n\n',
          'event: delta\ndata: {"text":"declined."}\n\n',
          'event: done\ndata: {"message_id":"m2","provider":"fake","model":"fake-1","is_test_double":true,"finish_reason":"stop","input_tokens":10,"output_tokens":4,"latency_ms":12.5,"cost_usd":"0.000000"}\n\n',
        ]),
      ),
    );

    const events = await collect();

    expect(events.map((event) => event.type)).toEqual([
      "user_message",
      "delta",
      "delta",
      "done",
    ]);
    const text = events
      .filter((event) => event.type === "delta")
      .map((event) => event.text)
      .join("");
    expect(text).toBe("Revenue declined.");
  });

  it("reassembles frames split across chunk boundaries", async () => {
    // The network can split anywhere, including mid-frame and mid-JSON.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          "event: del",
          'ta\ndata: {"text":"Rev',
          'enue "}\n',
          '\nevent: delta\ndata: {"text":"fell."}\n\n',
        ]),
      ),
    );

    const events = await collect();

    expect(events).toHaveLength(2);
    expect(events.map((event) => (event.type === "delta" ? event.text : "")).join("")).toBe(
      "Revenue fell.",
    );
  });

  it("surfaces a server error event", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: delta\ndata: {"text":"partial"}\n\n',
          'event: error\ndata: {"code":"llm_rate_limited","message":"Rate limited.","partial":true}\n\n',
        ]),
      ),
    );

    const events = await collect();
    const last = events[events.length - 1];

    expect(last?.type).toBe("error");
    if (last?.type === "error") {
      expect(last.code).toBe("llm_rate_limited");
      expect(last.partial).toBe(true);
    }
  });

  it("ignores unknown event names instead of throwing", async () => {
    // Adding a server-side event must not break an older client.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: some_future_event\ndata: {"whatever":1}\n\n',
          'event: delta\ndata: {"text":"ok"}\n\n',
        ]),
      ),
    );

    const events = await collect();

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe("delta");
  });

  it("sends the bearer token and asks for an event stream", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(['event: delta\ndata: {"text":"x"}\n\n']));
    vi.stubGlobal("fetch", fetchMock);

    await collect();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(url).toBe("/api/v1/conversations/conversation-1/messages");
    expect(headers["Authorization"]).toBe("Bearer token-abc");
    expect(headers["Accept"]).toBe("text/event-stream");
  });

  it("retries once through a refresh when the stream is rejected as unauthorised", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ access_token: "fresh" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(sseResponse(['event: delta\ndata: {"text":"after refresh"}\n\n']));
    vi.stubGlobal("fetch", fetchMock);

    const events = await collect();

    expect(events).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
