"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MonitorCheck, Tv } from "lucide-react";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/matches", label: "Matches", icon: Tv, match: (p: string) => p.startsWith("/matches") },
  {
    href: "/producer",
    label: "Producer",
    icon: MonitorCheck,
    match: (p: string) => p.startsWith("/producer"),
  },
] as const;

function NavLinks({ pathname }: { pathname: string | null }) {
  return (
    <>
      {LINKS.map(({ href, label, icon: Icon, match }) => {
        const active = pathname !== null && match(pathname);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm whitespace-nowrap transition-colors",
              active
                ? "bg-slate-100 font-medium text-slate-900"
                : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
            )}
          >
            <Icon className="h-4 w-4" aria-hidden /> {label}
          </Link>
        );
      })}
    </>
  );
}

/** Links with the current page marked (reads the URL, so it renders inside Suspense). */
export function ActiveNavLinks() {
  return <NavLinks pathname={usePathname()} />;
}

/** The same links before the URL is known (the Suspense fallback while prerendering). */
export function PlainNavLinks() {
  return <NavLinks pathname={null} />;
}
