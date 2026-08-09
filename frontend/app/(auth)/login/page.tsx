import type { Metadata } from "next";

import { LoginForm } from "@/features/auth/login-form";

export const metadata: Metadata = { title: "Sign in · InsightAgent" };

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight">InsightAgent</h1>
          <p className="mt-1 text-sm text-[--color-ink-muted]">
            Autonomous research and data analysis
          </p>
        </div>

        <div className="rounded-lg border border-[--color-border] bg-white p-6 shadow-sm">
          <LoginForm />
        </div>

        <p className="mt-6 text-center text-xs text-[--color-ink-muted]">
          All business data in this environment is synthetic.
        </p>
      </div>
    </main>
  );
}
