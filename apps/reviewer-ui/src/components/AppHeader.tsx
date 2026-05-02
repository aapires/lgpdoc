"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { OpfToggle } from "./OpfToggle";

const NAV = [
  { href: "/jobs", label: "Documentos" },
  { href: "/containers", label: "Containers" },
  { href: "/settings", label: "Configurações" },
];

export function AppHeader() {
  const pathname = usePathname();
  return (
    <header className="app">
      <Link href="/jobs" className="brand" style={{ color: "inherit" }}>
        🛡️
        <span className="brand-text">
          LGPDoc
          <small className="brand-version">v1.0</small>
        </span>
      </Link>
      <div className="header-right">
        <nav className="nav">
          {NAV.map((item) => {
            const active =
              pathname === item.href ||
              (item.href !== "/" && pathname.startsWith(item.href));
            return (
              <Link
                key={item.href}
                href={item.href}
                className={active ? "active" : undefined}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <OpfToggle />
      </div>
    </header>
  );
}
