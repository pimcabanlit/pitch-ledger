import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, Calculator, FileSearch, ShieldCheck, UserCheck } from "lucide-react";

import { HeroArt } from "@/components/hero-art";
import { Card } from "@/components/ui/card";

export const metadata: Metadata = { title: "For producers · Pitch Ledger Studio" };

const TRUST = [
  {
    icon: Calculator,
    title: "Numbers from code, never a model",
    text: "Every figure is computed in deterministic Python. The agents explain; they do not calculate.",
  },
  {
    icon: FileSearch,
    title: "Evidence on every insight",
    text: "Each insight cites the match events behind it, so anyone can trace a claim to its source.",
  },
  {
    icon: ShieldCheck,
    title: "An Auditor before air",
    text: "The Auditor recomputes every number from the raw events and blocks text that claims more than they show.",
  },
  {
    icon: UserCheck,
    title: "A producer decides",
    text: "A producer approves or blocks each story. Quick stat badges are approved by a fixed rule.",
  },
] as const;

/** The producer's landing page: why the insights can be trusted, then into the console. */
export default function ForProducersPage() {
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-gradient-to-br from-white via-white to-emerald-50">
      <div className="grid items-center gap-8 px-6 py-10 sm:px-10 md:py-12 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div>
          <h1 className="max-w-xl text-3xl font-bold tracking-tight text-balance sm:text-4xl lg:text-5xl">
            Match intelligence you can audit before it airs
          </h1>
          <p className="mt-4 max-w-xl text-slate-600">
            Review each insight with its evidence and audit trail, then approve it to air in every
            mode and language, or block it.
          </p>
          <Link
            href="/producer"
            className="mt-6 inline-flex h-11 items-center justify-center gap-2 rounded-md bg-slate-900 px-5 text-sm font-medium text-white hover:bg-slate-700"
          >
            Try it now <ArrowRight className="h-4 w-4" aria-hidden />
          </Link>

          <div id="how-trust-works" className="mt-10 scroll-mt-20">
            <h2 className="font-semibold">How trust works</h2>
            <p className="text-sm text-slate-600">
              The ledger keeps every insight with its numbers, evidence, audit trail and approval.
            </p>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              {TRUST.map(({ icon: Icon, title, text }) => (
                <Card key={title} className="flex gap-3 p-4">
                  <Icon className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" aria-hidden />
                  <div>
                    <h3 className="text-sm font-medium">{title}</h3>
                    <p className="mt-1 text-sm text-slate-600">{text}</p>
                  </div>
                </Card>
              ))}
            </div>
          </div>
        </div>
        <HeroArt className="mx-auto hidden w-full max-w-md lg:block" />
      </div>
    </section>
  );
}
