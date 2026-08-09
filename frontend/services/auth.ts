import { apiRequest, refreshAccessToken, setAccessToken } from "@/services/api-client";
import type { AccessToken, LoginCredentials, RegisterPayload, User } from "@/types/auth";

export async function login(credentials: LoginCredentials): Promise<User> {
  const token = await apiRequest<AccessToken>("/auth/login", {
    method: "POST",
    body: credentials,
    skipRefresh: true,
  });
  setAccessToken(token.access_token);
  return getCurrentUser();
}

export async function register(payload: RegisterPayload): Promise<User> {
  return apiRequest<User>("/auth/register", {
    method: "POST",
    body: payload,
    skipRefresh: true,
  });
}

export async function logout(): Promise<void> {
  try {
    await apiRequest<void>("/auth/logout", { method: "POST", skipRefresh: true });
  } finally {
    // The local session is dropped even if the server call fails, so a network
    // error cannot leave the UI appearing signed in.
    setAccessToken(null);
  }
}

export async function getCurrentUser(): Promise<User> {
  return apiRequest<User>("/auth/me");
}

/**
 * Restore a session on page load from the HttpOnly refresh cookie.
 * Returns null when there is no valid session — the normal signed-out case.
 */
export async function restoreSession(): Promise<User | null> {
  if (!(await refreshAccessToken())) return null;
  try {
    return await getCurrentUser();
  } catch {
    setAccessToken(null);
    return null;
  }
}
