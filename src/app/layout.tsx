import type { Metadata } from "next";
import Link from "next/link";
import { refreshData } from "./actions";
import "./globals.css";

export const metadata: Metadata = {
  title: "FinanceBuddy",
  description: "What to do with the check that just landed.",
};

const PAGES = [
  ["/payday", "Payday"],
  ["/log", "Log"],
  ["/bills", "Bills"],
  ["/transactions", "Transactions"],
  ["/budgets", "Budgets"],
  ["/accounts", "Accounts"],
  ["/savings", "Savings"],
  ["/wishlist", "Wishlist"],
  ["/import", "Import"],
] as const;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen md:flex">
        <nav className="border-b border-[var(--border)] bg-[var(--panel)] px-4 py-4 md:w-52 md:shrink-0 md:border-r md:border-b-0">
          <Link href="/" className="text-lg font-semibold">FinanceBuddy</Link>
          <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 md:flex-col">
            {PAGES.map(([href, label]) => (
              <li key={href}>
                <Link href={href} className="text-[var(--muted)] hover:text-[var(--ink)]">{label}</Link>
              </li>
            ))}
          </ul>
          <form action={refreshData} className="mt-4 hidden md:block">
            <button className="w-full rounded-md border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--bg)]">🔄 Refresh data</button>
            <p className="mt-1 text-xs text-[var(--muted)]">Cached for five minutes. Refresh after editing the sheet by hand.</p>
          </form>
        </nav>
        <main className="mx-auto w-full max-w-5xl px-4 py-6 md:px-8">{children}</main>
      </body>
    </html>
  );
}
