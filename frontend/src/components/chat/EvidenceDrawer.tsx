import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";

export interface EvidenceRef { id: string; ids: string[]; tool: string; focus?: string }

interface Fix { field: string; original: unknown; corrected: unknown; by: string | null; reason: string | null }
interface Doc {
  id: string; kind: string; label: string; asset_id: string; file: string; line: number | null;
  record: Record<string, unknown>; highlight: string[]; how: string; marks: string[];
  fixes: Fix[]; related: { id: string; label: string; summary: string }[];
  report: { file: string; text: string } | null; error?: string;
}

const fmt = (v: unknown) => (v !== null && typeof v === "object" ? JSON.stringify(v) : String(v));

/** Wrap every occurrence of any mark in <mark>, case-insensitively. */
function marked(text: string, marks: string[]): ReactNode {
  const list = marks.filter(Boolean);
  if (!list.length) return text;
  const re = new RegExp(`(${list.map((m) => m.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.split(re).map((part, i) =>
    i % 2 ? <mark key={i} data-hit className="mo-mark">{part}</mark> : <Fragment key={i}>{part}</Fragment>,
  );
}

/** Minimal markdown: headings, bold, paragraphs. Enough for the repair reports. */
function Report({ text, marks }: { text: string; marks: string[] }) {
  const inline = (s: string) =>
    s.split(/(\*\*[^*]+\*\*)/g).map((p, i) => (p.startsWith("**") ? <strong key={i} className="text-ink">{marked(p.slice(2, -2), marks)}</strong> : <Fragment key={i}>{marked(p, marks)}</Fragment>));
  // The reports are hard-wrapped: join wrapped lines into one paragraph, keep the "**Key:** value" header lines apart.
  const blocks: { kind: "h2" | "h1" | "p"; text: string }[] = [];
  let open = false;
  for (const line of text.split("\n")) {
    if (line.startsWith("## ")) { blocks.push({ kind: "h2", text: line.slice(3) }); open = false; }
    else if (line.startsWith("# ")) { blocks.push({ kind: "h1", text: line.slice(2) }); open = false; }
    else if (!line.trim()) open = false;
    else if (open && !line.startsWith("**")) blocks[blocks.length - 1].text += ` ${line.trim()}`;
    else { blocks.push({ kind: "p", text: line.trim() }); open = !line.startsWith("**"); }
  }
  return (
    <div className="flex flex-col gap-2 text-[13px] leading-relaxed text-ink-2">
      {blocks.map((b, i) =>
        b.kind === "h2" ? <h4 key={i} className="mt-2 text-sm font-semibold text-ink">{b.text}</h4>
        : b.kind === "h1" ? <h3 key={i} className="text-sm font-semibold text-ink">{b.text}</h3>
        : <p key={i}>{inline(b.text)}</p>,
      )}
    </div>
  );
}

export function EvidenceDrawer({ refd, onNavigate, onClose }: { refd: EvidenceRef; onNavigate: (r: EvidenceRef) => void; onClose: () => void }) {
  const [doc, setDoc] = useState<Doc | null>(null);
  const [failed, setFailed] = useState("");
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    setDoc(null); setFailed("");
    const q = new URLSearchParams({ tool: refd.tool, focus: refd.focus ?? "" });
    fetch(`/api/evidence/${encodeURIComponent(refd.id)}?${q}`)
      .then((r) => r.json())
      .then((d: Doc) => { if (!live) return; d.error ? setFailed(d.error) : setDoc(d); })
      .catch(() => live && setFailed("Could not load this record."));
    return () => { live = false; };
  }, [refd.id, refd.tool, refd.focus]);

  // bring the first highlighted part into view once the document is on screen
  useEffect(() => {
    if (!doc) return;
    const t = window.setTimeout(() => body.current?.querySelector("[data-hit]")?.scrollIntoView({ block: "center", behavior: "smooth" }), 120);
    return () => window.clearTimeout(t);
  }, [doc]);

  const at = refd.ids.indexOf(refd.id);
  const step = (d: number) => { const n = refd.ids[at + d]; if (n) onNavigate({ ...refd, id: n, focus: undefined }); };

  return (
    <aside className="flex h-full w-full flex-col border-l border-line bg-surface md:w-[440px]" aria-label="Evidence">
      <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-wide text-ink-3">{doc?.label ?? "Evidence"}</div>
          <div className="truncate font-mono text-sm text-ink">{refd.id}</div>
        </div>
        <button onClick={onClose} aria-label="Close evidence" className="ui-pill !px-2.5">✕</button>
      </div>

      {refd.ids.length > 1 && !refd.focus && (
        <div className="flex items-center gap-2 border-b border-line px-4 py-2 text-xs text-ink-3">
          <button className="ui-pill" disabled={at <= 0} onClick={() => step(-1)} aria-label="Previous evidence">←</button>
          <span className="tabular-nums">{at + 1} of {refd.ids.length}</span>
          <button className="ui-pill" disabled={at < 0 || at >= refd.ids.length - 1} onClick={() => step(1)} aria-label="Next evidence">→</button>
        </div>
      )}

      <div ref={body} className="min-h-0 flex-1 overflow-y-auto p-4">
        {failed && <p className="text-sm text-danger">{failed}</p>}
        {!doc && !failed && <div className="flex flex-col gap-2">{[70, 90, 55, 80].map((w, i) => <div key={i} className="h-4 animate-pulse rounded bg-white/5" style={{ width: `${w}%` }} />)}</div>}

        {doc && (
          <div className="flex flex-col gap-4">
            <section className="rounded-xl border border-accent/25 bg-accent/10 p-3">
              <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-accent">How this was used</div>
              <p className="text-[13px] leading-relaxed text-ink">{doc.how}</p>
            </section>

            {doc.fixes.map((f) => (
              <section key={f.field} className="rounded-xl border border-warn/30 bg-warn/10 p-3 text-[13px] text-ink-2">
                <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-warn">Corrected</div>
                <span className="font-mono">{f.field}</span>: <s className="text-ink-3">{fmt(f.original)}</s> → <strong className="text-ink">{fmt(f.corrected)}</strong>
                <div className="mt-1 text-xs text-ink-3">by {f.by ?? "unknown"}{f.reason ? ` · ${f.reason}` : ""}. The original is kept.</div>
              </section>
            ))}

            <section>
              <div className="mb-2 flex items-center justify-between text-xs text-ink-3">
                <span className="truncate font-mono">{doc.file}{doc.line ? `:${doc.line}` : ""}</span>
                <span className="shrink-0 pl-2">highlighted = used</span>
              </div>
              <div className="overflow-hidden rounded-xl border border-line bg-canvas font-mono text-[12px] leading-6">
                {Object.entries(doc.record).map(([k, v]) => {
                  const used = doc.highlight.includes(k);
                  return (
                    <div key={k} data-hit={used ? "" : undefined}
                      className={`grid grid-cols-[132px_1fr] gap-3 border-l-2 px-3 ${used ? "mo-hit border-accent bg-accent/10 text-ink" : "border-transparent text-ink-3"}`}>
                      <span className="truncate" title={k}>{k}</span>
                      <span className="break-words">{marked(fmt(v), doc.marks)}</span>
                    </div>
                  );
                })}
              </div>
            </section>

            {doc.report && (
              <section>
                <div className="mb-2 truncate font-mono text-xs text-ink-3">{doc.report.file}</div>
                <div className="rounded-xl border border-line bg-canvas p-4"><Report text={doc.report.text} marks={doc.marks} /></div>
              </section>
            )}

            {doc.related.length > 0 && (
              <section>
                <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-ink-3">Linked records</div>
                <div className="flex flex-col gap-1.5">
                  {doc.related.map((r) => (
                    <button key={r.id} className="ui-card flex flex-col items-start px-3 py-2 text-left transition hover:bg-white/[0.08]"
                      onClick={() => onNavigate({ ...refd, ids: refd.ids.includes(r.id) ? refd.ids : [r.id, ...refd.ids], id: r.id, focus: doc.id })}>
                      <span className="text-xs text-ink"><span className="font-mono">{r.id}</span> <span className="text-ink-3">· {r.label}</span></span>
                      <span className="text-xs text-ink-3">{r.summary}</span>
                    </button>
                  ))}
                </div>
              </section>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
