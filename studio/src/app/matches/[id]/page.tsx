import { Suspense } from "react";

import { FanView } from "@/components/fan-view";

export default function MatchPage({ params }: PageProps<"/matches/[id]">) {
  return (
    <Suspense fallback={<p className="text-sm text-slate-500">Loading match…</p>}>
      <FanView params={params} />
    </Suspense>
  );
}
