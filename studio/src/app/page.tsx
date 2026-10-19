import Link from "next/link";
import {
  ArrowRight,
  Calculator,
  Languages,
  MonitorCheck,
  MonitorPlay,
  Radio,
  Sparkles,
  Tv,
} from "lucide-react";

import { HeroArt } from "@/components/hero-art";
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

export default function Home() {
  return (
    <div className="space-y-12">
      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-gradient-to-br from-white via-white to-emerald-50">
        <div className="grid items-center gap-2 px-6 pt-10 pb-4 sm:px-10 md:grid-cols-2 md:gap-8 md:py-12">
          <div>
            <h1 className="text-4xl font-bold tracking-tight text-balance sm:text-5xl lg:text-6xl">Pitch Ledger Studio</h1>
            <p className="mt-4 text-lg font-medium text-balance text-slate-700 sm:text-xl">
              Where match intelligence meets fan experience.
            </p>
            <p className="mt-3 max-w-md text-sm text-slate-600">
              Follow every big moment live, with stats you can trust, explained in plain words, in
              your style and your language.
            </p>
            <div className="mt-7 flex flex-col gap-3 sm:flex-row">
              <Link
                href="/matches/M0002"
                className="inline-flex h-11 items-center justify-center gap-2 rounded-md bg-slate-900 px-5 text-sm font-medium text-white hover:bg-slate-700"
              >
                <Tv className="h-4 w-4" aria-hidden /> Watch the demo match
              </Link>
              <Link
                href="/for-producers"
                className="inline-flex h-11 items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-5 text-sm font-medium hover:bg-slate-100"
              >
                <MonitorCheck className="h-4 w-4" aria-hidden /> I&rsquo;m a producer
              </Link>
            </div>
          </div>
          <HeroArt className="mx-auto w-full max-w-sm md:max-w-none" />
        </div>
      </section>

      <section id="how-it-works" aria-labelledby="pipeline" className="scroll-mt-20 space-y-4">
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

    </div>
  );
}
