import { Suspense } from "react";

import { StoryRedirect } from "@/components/insight-story";

export default function InsightStoryPage({ params }: PageProps<"/insights/[id]">) {
  return (
    <Suspense fallback={<p className="text-sm text-slate-500">Opening the story…</p>}>
      <StoryRedirect params={params} />
    </Suspense>
  );
}
