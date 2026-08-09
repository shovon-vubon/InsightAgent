import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest, getAccessToken, setAccessToken } from "@/services/api-client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiRequest", () => {
  beforeEach(() => {
    setAccessToken(null);
  });

  it("attaches the bearer token when one is held", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("token-abc");

    await apiRequest("/auth/me");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer token-abc");
  });

  it("sends no Authorization header when signed out", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest("/health/live");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Authorization"]).toBeUndefined();
  });

  it("refreshes once on a 401 and replays the original request", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ error: { code: "authentication_failed" } }, 401))
      .mockResolvedValueOnce(jsonResponse({ access_token: "fresh-token" }))
      .mockResolvedValueOnce(jsonResponse({ email: "user@example.com" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await apiRequest<{ email: string }>("/auth/me");

    expect(result.email).toBe("user@example.com");
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/v1/auth/refresh");
    expect(getAccessToken()).toBe("fresh-token");
  });

  it("gives up and clears the session when the refresh also fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ error: { code: "authentication_failed" } }, 401))
      .mockResolvedValueOnce(jsonResponse({ error: { code: "authentication_failed" } }, 401));
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("stale-token");

    await expect(apiRequest("/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(getAccessToken()).toBeNull();
    // Exactly two calls: the original and one refresh attempt. No retry storm.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not attempt a refresh when the caller opted out", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ error: { code: "authentication_failed" } }, 401));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiRequest("/auth/login", { method: "POST", skipRefresh: true })).rejects.toThrow(
      ApiError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("surfaces the API error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: {
              code: "conflict",
              message: "An account with that email already exists.",
              request_id: "req-123",
            },
          },
          409,
        ),
      ),
    );

    const error = await apiRequest("/auth/register", {
      method: "POST",
      skipRefresh: true,
    }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(409);
    expect(apiError.code).toBe("conflict");
    expect(apiError.requestId).toBe("req-123");
  });

  it("handles a 204 with no body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    await expect(
      apiRequest("/auth/logout", { method: "POST", skipRefresh: true }),
    ).resolves.toBeUndefined();
  });

  it("shares one refresh across concurrent 401s", async () => {
    // Two parallel rotations would make the second replay a spent refresh token
    // and trip the backend's reuse detection, logging the user out.
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === "/api/v1/auth/refresh") {
        return Promise.resolve(jsonResponse({ access_token: "fresh-token" }));
      }
      return Promise.resolve(
        getAccessToken() === "fresh-token"
          ? jsonResponse({ ok: true })
          : jsonResponse({ error: { code: "authentication_failed" } }, 401),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    await Promise.all([apiRequest("/a"), apiRequest("/b"), apiRequest("/c")]);

    const refreshCalls = fetchMock.mock.calls.filter(
      ([url]) => url === "/api/v1/auth/refresh",
    ).length;
    expect(refreshCalls).toBe(1);
  });
});
