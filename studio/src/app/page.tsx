import Link from "next/link";
import {
  ArrowRight,
  Calculator,
  FileSearch,
  Languages,
  MonitorCheck,
  MonitorPlay,
  Radio,
  ShieldCheck,
  Sparkles,
  Tv,
  UserCheck,
} from "lucide-react";

import { MatchList } from "@/components/match-list";
import { Card } from "@/components/ui/card";

const STAGES = [
  {
    icon: Radio,
    name: "Ingest",
    text: "A live feed of synthetic match events and 1-second player tracking.",
  },
  {
    icon: Calculator,
    name: "Interpret",
    text: "Python computes control, chaos, tempo, xG and speed, and detects the moments worth telling.",
  },
  {
    icon: Sparkles,
    name: "Explain",
    text: "AI agents write each insight from those numbers only, through read-only stats tools.",
  },
  {
    icon: MonitorPlay,
    name: "Render",
    text: "Approved insights become broadcast overlays with timing and priority.",
  },
  {
    icon: Languages,
    name: "Personalize",
    text: "Each moment is rewritten for five audiences in English, Spanish and Filipino.",
  },
] as const;

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

export default function Home() {
  return (
    <div className="space-y-12">
      <section className="rounded-xl border border-slate-200 bg-gradient-to-br from-white via-white to-emerald-50 px-5 py-8 sm:px-8 sm:py-10">
        <p className="text-sm font-medium text-emerald-700">Insight ledger for match studios</p>
        <h1 className="mt-2 max-w-3xl text-3xl font-semibold tracking-tight text-balance sm:text-4xl">
          Match intelligence you can audit, before it airs.
        </h1>
        <p className="mt-3 max-w-2xl text-slate-600">
          Pitch Ledger turns a live match feed into explained insights for studio, broadcast and
          streaming. Code computes every number, AI agents explain it, an Auditor checks it against
          the evidence, and a producer approves it. Then each viewer gets it in their own style
          and language.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Link
            href="/matches/M0002"
            className="inline-flex h-10 items-center gap-2 rounded-md bg-slate-900 px-4 text-sm font-medium text-white hover:bg-slate-700"
          >
            <Tv className="h-4 w-4" aria-hidden /> Watch the demo match
          </Link>
          <Link
            href="/producer"
            className="inline-flex h-10 items-center gap-2 rounded-md border border-slate-300 bg-white px-4 text-sm font-medium hover:bg-slate-100"
          >
            <MonitorCheck className="h-4 w-4" aria-hidden /> Producer console
          </Link>
        </div>
        <p className="mt-4 text-xs text-slate-500">
          The demo match has a planted late collapse: watch the shift get detected, explained,
          audited and approved.
        </p>
      </section>

      <section aria-labelledby="pipeline" className="space-y-4">
        <h2 id="pipeline" className="text-xl font-semibold">
          From feed to fan in five steps
        </h2>
        <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {STAGES.map(({ icon: Icon, name, text }, i) => (
            <li key={name}>
              <Card className="h-full p-4">
                <div className="flex items-center gap-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-emerald-50 text-emerald-700">
                    <Icon className="h-4 w-4" aria-hidden />
                  </span>
                  <span className="text-xs text-slate-500">{i + 1}</span>
                  <h3 className="font-medium">{name}</h3>
                </div>
                <p className="mt-2 text-sm text-slate-600">{text}</p>
              </Card>
            </li>
          ))}
        </ol>
      </section>

      <section id="how-it-works" aria-labelledby="trust" className="scroll-mt-20 space-y-4">
        <div>
          <h2 id="trust" className="text-xl font-semibold">
            How trust works
          </h2>
          <p className="text-sm text-slate-600">
            The ledger keeps every insight with its numbers, evidence, audit trail and approval.
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {TRUST.map(({ icon: Icon, title, text }) => (
            <Card key={title} className="flex gap-3 p-4">
              <Icon className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" aria-hidden />
              <div>
                <h3 className="font-medium">{title}</h3>
                <p className="mt-1 text-sm text-slate-600">{text}</p>
              </div>
            </Card>
          ))}
        </div>
      </section>

      <section aria-labelledby="matches" className="space-y-4">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h2 id="matches" className="text-xl font-semibold">
              Matches
            </h2>
            <p className="text-sm text-slate-600">
              Replay a match on its clock with captions, charts and a pitch, in your mode and
              language.
            </p>
          </div>
          <Link
            href="/matches"
            className="flex shrink-0 items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
          >
            All matches <ArrowRight className="h-4 w-4" aria-hidden />
          </Link>
        </div>
        <MatchList />
      </section>

      <section aria-labelledby="views" className="grid gap-3 sm:grid-cols-2">
        <h2 id="views" className="sr-only">
          Two views
        </h2>
        <Link href="/matches" className="group">
          <Card className="h-full p-5 transition-shadow group-hover:shadow-md">
            <Tv className="h-5 w-5 text-sky-600" aria-hidden />
            <h3 className="mt-2 font-medium">Fan view</h3>
            <p className="mt-1 text-sm text-slate-600">
              Pick your club, follow a player and choose the stat you care about. Your choices are
              remembered on this device.
            </p>
          </Card>
        </Link>
        <Link href="/producer" className="group">
          <Card className="h-full p-5 transition-shadow group-hover:shadow-md">
            <MonitorCheck className="h-5 w-5 text-amber-600" aria-hidden />
            <h3 className="mt-2 font-medium">Producer console</h3>
            <p className="mt-1 text-sm text-slate-600">
              Review pending insights with their evidence and audit trail, approve or block them,
              and keep an eye on feed and model alerts.
            </p>
          </Card>
        </Link>
      </section>
    </div>
  );
}
