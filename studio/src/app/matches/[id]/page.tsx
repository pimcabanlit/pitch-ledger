import { Suspense } from "react";

import { MatchCentre } from "@/components/match-centre/match-centre";

export default function MatchPage({ params }: PageProps<"/matches/[id]">) {
  return (
    <Suspense fallback={<p className="text-sm text-slate-500">Loading the match…</p>}>
      <MatchCentre params={params} />
    </Suspense>
  );
}
