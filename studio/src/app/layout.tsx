import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { Suspense } from "react";
import { BookCheck } from "lucide-react";

import { ActiveNavLinks, PlainNavLinks } from "@/components/site-nav";

import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Pitch Ledger Studio",
  description: "Audited, personalized match intelligence from synthetic football data.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
      <body className="flex min-h-screen flex-col">
        <a
          href="#main"
          className="sr-only z-50 rounded-md bg-slate-900 px-3 py-2 text-sm text-white focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
        >
          Skip to content
        </a>
        <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/95 backdrop-blur">
          <nav
            aria-label="Main"
            className="mx-auto flex max-w-6xl items-center gap-1 px-4 py-2 sm:gap-3"
          >
            <Link
              href="/"
              className="mr-auto flex items-center gap-2 whitespace-nowrap font-semibold sm:mr-3"
            >
              <BookCheck className="h-5 w-5 text-emerald-600" aria-hidden />
              Pitch Ledger<span className="hidden sm:inline"> Studio</span>
            </Link>
            <Suspense fallback={<PlainNavLinks />}>
              <ActiveNavLinks />
            </Suspense>
            <span className="ml-auto hidden text-xs text-slate-500 md:inline">
              Synthetic data · fictional clubs
            </span>
          </nav>
        </header>
        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
          {children}
        </main>
        <footer className="border-t border-slate-200 bg-white">
          <div className="mx-auto flex max-w-6xl flex-col gap-1 px-4 py-4 text-xs text-slate-500 sm:flex-row sm:justify-between">
            <p>
              Synthetic matches with fictional clubs and players. Every number is computed in code
              and checked by the Auditor before it airs.
            </p>
            <p className="whitespace-nowrap">
              <Link href="/#how-it-works" className="hover:text-slate-900">
                How it works
              </Link>
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
