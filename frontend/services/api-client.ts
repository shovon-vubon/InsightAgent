/**
 * The single place the frontend talks to the API.
 *
 * Components never call `fetch` directly — they go through feature-level services
 * that use this client, so auth handling and error shaping live in exactly one
 * place.
 *
 * Access token: held in memory only. Deliberately not in `localStorage`, where any
 * XSS payload could read it. It is re-obtained on page load from the HttpOnly
 * refresh cookie.
 */

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    request_id?: string;
    details?: unknown;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | undefined;
  readonly details: unknown;

  constructor(status: number, body: ApiErrorBody | null, fallback: string) {
    super(body?.error?.message ?? fallback);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.error?.code ?? "unknown_error";
    this.requestId = body?.error?.request_id;
    this.details = body?.error?.details;
  }
}

const API_PREFIX = "/api/v1";

let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  /** Internal: prevents a refresh failure from recursing forever. */
  skipRefresh?: boolean;
  signal?: AbortSignal;
}

/**
 * In-flight refresh, shared so that N concurrent 401s trigger one rotation.
 * Rotating twice in parallel would make the second call replay a spent token and
 * trip the backend's reuse detection, logging the user out.
 */
let refreshInFlight: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  refreshInFlight ??= (async () => {
    try {
      const response = await fetch(`${API_PREFIX}/auth/refresh`, {
        method: "POST",
        credentials: "same-origin",
      });
      if (!response.ok) {
        setAccessToken(null);
        return false;
      }
      const data = (await response.json()) as { access_token: string };
      setAccessToken(data.access_token);
      return true;
    } catch {
      setAccessToken(null);
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

async function parseErrorBody(response: Response): Promise<ApiErrorBody | null> {
  try {
    return (await response.json()) as ApiErrorBody;
  } catch {
    return null;
  }
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, skipRefresh = false, signal } = options;

  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;

  const requestInit: RequestInit = {
    method,
    headers,
    credentials: "same-origin",
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    ...(signal ? { signal } : {}),
  };

  const response = await fetch(`${API_PREFIX}${path}`, requestInit);

  // One retry after a silent refresh. `skipRefresh` guards the auth endpoints
  // themselves, whose 401 is a genuine answer rather than an expired token.
  if (response.status === 401 && !skipRefresh) {
    if (await refreshAccessToken()) {
      return apiRequest<T>(path, { ...options, skipRefresh: true });
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorBody(response), response.statusText);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * Authenticated fetch that returns the raw `Response` with its body unread.
 *
 * Streaming endpoints cannot go through `apiRequest`, which consumes the body as
 * JSON. `EventSource` is not an option either: it cannot set an `Authorization`
 * header, and the access token deliberately lives in memory rather than a cookie.
 */
export async function apiStream(
  path: string,
  options: { method?: "POST" | "GET"; body?: unknown; signal?: AbortSignal } = {},
): Promise<Response> {
  const { method = "POST", body, signal } = options;

  const send = async (): Promise<Response> => {
    const headers: Record<string, string> = { Accept: "text/event-stream" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;

    return fetch(`${API_PREFIX}${path}`, {
      method,
      headers,
      credentials: "same-origin",
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      ...(signal ? { signal } : {}),
    });
  };

  let response = await send();
  if (response.status === 401 && (await refreshAccessToken())) {
    response = await send();
  }

  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorBody(response), response.statusText);
  }
  return response;
}

export { refreshAccessToken };
