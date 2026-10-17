import { useEffect, useState } from "react";
import { DotMorph, type SkeletonKind } from "@/components/a2ui/DotMorph";

/** Dev-only: /?skel=BarChart holds the dotted skeleton, then morphs after ?at=<seconds>. */
export default function SkeletonPreview({ kind, at }: { kind: SkeletonKind; at: number }) {
  const [ready, setReady] = useState(false);
  useEffect(() => { const t = window.setTimeout(() => setReady(true), at * 1000); return () => window.clearTimeout(t); }, [at]);
  return (
    <div className="grid h-full place-items-center p-6">
      <div className="w-full max-w-3xl">
        <DotMorph kind={kind} ready={ready}>
          <div className="ui-card grid h-72 place-items-center text-ink-2">The real component</div>
        </DotMorph>
      </div>
    </div>
  );
}
