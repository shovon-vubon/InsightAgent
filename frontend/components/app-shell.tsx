"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/auth-context";

interface NavItem {
  href: string;
  label: string;
  /** Phase that delivers the feature; shown until it exists (brief §61.10). */
  plannedIn?: string;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/research", label: "Research", plannedIn: "Phase 7" },
  { href: "/knowledge", label: "Knowledge base", plannedIn: "Phase 3" },
  { href: "/datasets", label: "Datasets", plannedIn: "Phase 6" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();

  async function handleLogout() {
    await logout();
    router.replace("/login");
  }

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-64 shrink-0 flex-col bg-[--color-shell] text-[--color-shell-ink]">
        <div className="px-5 py-5">
          <p className="text-base font-semibold text-white">InsightAgent</p>
          <p className="mt-0.5 text-xs opacity-70">Research workspace</p>
        </div>

        <nav className="flex flex-1 flex-col gap-1 px-3" aria-label="Main">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive ? "page" : undefined}
                className={clsx(
                  "flex items-center justify-between rounded-md px-3 py-2 text-sm transition-colors",
                  isActive ? "bg-white/10 text-white" : "hover:bg-white/5",
                )}
              >
                <span>{item.label}</span>
                {item.plannedIn ? (
                  <span className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide opacity-70">
                    {item.plannedIn}
                  </span>
                ) : null}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-white/10 px-5 py-4">
          <p className="truncate text-xs opacity-70">{user?.email}</p>
          <p className="mt-0.5 text-[10px] uppercase tracking-wide opacity-50">{user?.role}</p>
          <Button variant="ghost" className="mt-3 w-full" onClick={() => void handleLogout()}>
            Sign out
          </Button>
        </div>
      </aside>

      <main className="flex-1 overflow-auto">{children}</main>
    </div>
  );
}
