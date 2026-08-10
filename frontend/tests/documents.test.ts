import { beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken } from "@/services/api-client";
import {
  askKnowledgeBase,
  formatBytes,
  formatLocation,
  uploadDocument,
} from "@/services/documents";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("formatBytes", () => {
  it.each([
    [512, "512 B"],
    [2048, "2 KB"],
    [5 * 1024 * 1024, "5.0 MB"],
  ])("formats %i as %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });
});

describe("formatLocation", () => {
  it("renders a single page", () => {
    expect(
      formatLocation({ page_from: 4, page_to: 4, section_path: null }),
    ).toBe("p. 4");
  });

  it("renders a page range", () => {
    expect(formatLocation({ page_from: 4, page_to: 6, section_path: null })).toBe("pp. 4–6");
  });

  it("falls back to the section path when there are no pages", () => {
    // DOCX and Markdown have no pagination, so provenance is the section trail.
    expect(
      formatLocation({ page_from: null, page_to: null, section_path: "Q2 > Revenue" }),
    ).toBe("Q2 > Revenue");
  });

  it("combines page and section", () => {
    expect(
      formatLocation({ page_from: 2, page_to: null, section_path: "EMEA" }),
    ).toBe("p. 2 · EMEA");
  });
});

describe("uploadDocument", () => {
  beforeEach(() => {
    setAccessToken("test-token");
    vi.restoreAllMocks();
  });

  it("sends multipart form data without a Content-Type header", async () => {
    // The browser must set Content-Type itself so the multipart boundary is
    // correct; supplying one produces a body the server cannot parse.
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        jsonResponse({
          document_id: "doc-1",
          filename: "q2.pdf",
          status: "UPLOADED",
          size_bytes: 10,
          duplicate: false,
        }),
      );

    const file = new File([new Uint8Array([1, 2, 3])], "q2.pdf", { type: "application/pdf" });
    const result = await uploadDocument(file);

    expect(result.document_id).toBe("doc-1");

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.body).toBeInstanceOf(FormData);
    expect(Object.keys(init.headers as Record<string, string>)).not.toContain("Content-Type");
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer test-token");
  });

  it("surfaces a validation failure as an ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        { error: { code: "validation_error", message: "Unsupported file type." } },
        422,
      ),
    );

    const file = new File([new Uint8Array([1])], "payload.exe");
    await expect(uploadDocument(file)).rejects.toThrow("Unsupported file type.");
  });
});

describe("askKnowledgeBase", () => {
  beforeEach(() => {
    setAccessToken("test-token");
    vi.restoreAllMocks();
  });

  it("omits document_ids when none are selected", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ answer: "x", citations: [] }));

    await askKnowledgeBase("what happened to revenue");

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({ question: "what happened to revenue" });
  });

  it("includes document_ids when the search is scoped", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ answer: "x", citations: [] }));

    await askKnowledgeBase("revenue", ["doc-1", "doc-2"]);

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(init.body as string).document_ids).toEqual(["doc-1", "doc-2"]);
  });
});
