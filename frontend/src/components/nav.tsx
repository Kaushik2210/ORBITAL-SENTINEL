"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth-store";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/scenarios", label: "Scenarios" },
  { href: "/incidents", label: "Incidents" },
];

export function Nav() {
  const pathname = usePathname();
  const { email, role, clear } = useAuth();

  return (
    <header className="hud-panel sticky top-0 z-10 mx-4 mt-4 flex items-center justify-between rounded-lg border px-4 py-3 backdrop-blur">
      <div className="flex items-center gap-6">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-wide">
          <span className="text-accent">🛰</span>
          <span className="mono text-sm">ORBITAL SENTINEL</span>
        </Link>
        <nav className="flex gap-4 text-sm text-muted">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={
                pathname === l.href ? "text-accent" : "transition-colors hover:text-foreground"
              }
            >
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
      <div className="flex items-center gap-3 text-sm">
        {email ? (
          <>
            <span className="mono text-muted">
              {email} <span className="text-accent">({role})</span>
            </span>
            <button onClick={clear} className="text-muted transition-colors hover:text-danger">
              sign out
            </button>
          </>
        ) : (
          <Link href="/login" className="text-accent hover:underline">
            sign in
          </Link>
        )}
      </div>
    </header>
  );
}
