import { AppShell } from "@/components/app-shell";
import { RequireAuth } from "@/features/auth/require-auth";

export default function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <AppShell>{children}</AppShell>
    </RequireAuth>
  );
}
