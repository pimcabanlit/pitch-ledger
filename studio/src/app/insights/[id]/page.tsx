import { Suspense } from "react";

import { InsightStory } from "@/components/insight-story";

export default function InsightStoryPage({ params }: PageProps<"/insights/[id]">) {
  return (
    <Suspense fallback={<p className="text-sm text-slate-500">Loading the story…</p>}>
      <InsightStory params={params} />
    </Suspense>
  );
}
