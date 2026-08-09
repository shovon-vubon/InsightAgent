"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/features/auth/auth-context";

/**
 * Client-side gate for the authenticated shell.
 *
 * This is a UX affordance, not a security control. Every protected resource is
 * authorised server-side on each request; this only avoids rendering a shell the
 * user cannot populate.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && user === null) {
      router.replace("/login");
    }
  }, [isLoading, user, router]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-[--color-ink-muted]">Restoring session…</p>
      </div>
    );
  }

  if (user === null) return null;

  return <>{children}</>;
}
