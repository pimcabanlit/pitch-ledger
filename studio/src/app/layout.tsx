import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { BookCheck, Tv } from "lucide-react";

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
      <body className="min-h-screen">
        <header className="border-b border-slate-200 bg-white">
          <nav className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
            <Link href="/" className="flex items-center gap-2 font-semibold">
              <BookCheck className="h-5 w-5 text-emerald-600" aria-hidden />
              Pitch Ledger Studio
            </Link>
            <Link href="/" className="flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900">
              <Tv className="h-4 w-4" aria-hidden /> Matches
            </Link>
            <Link href="/producer" className="text-sm text-slate-600 hover:text-slate-900">
              Producer console
            </Link>
            <span className="ml-auto text-xs text-slate-500">Synthetic data · fictional clubs</span>
          </nav>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
