import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  AlertCircle,
  CheckSquare,
  FileCode2,
  Loader2,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import {
  lensChange,
  lensImpact,
  lensReview,
  lensUnresolved,
  lensErrorState,
} from "../../api/client";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import type {
  LensChangeRequest,
  LensEvidenceInfo,
  LensImpactRequest,
  LensKind,
  LensResponse,
  LensReviewRequest,
  LensUnresolvedRequest,
} from "../../types";

type LensBody =
  | LensChangeRequest
  | LensImpactRequest
  | LensReviewRequest
  | LensUnresolvedRequest;

interface LensPanelProps {
  open: boolean;
  onClose: () => void;
  repositoryId: number;
  kind: LensKind;
  title: string;
  description: string;
  body: LensBody;
  onOpenEvidence?: (evidence: LensEvidenceInfo) => void;
}

const RUNNERS: Record<
  LensKind,
  (repositoryId: number, body: LensBody) => Promise<LensResponse>
> = {
  change: (id, body) => lensChange(id, body as LensChangeRequest),
  impact: (id, body) => lensImpact(id, body as LensImpactRequest),
  review: (id, body) => lensReview(id, body as LensReviewRequest),
  unresolved: (id, body) => lensUnresolved(id, body as LensUnresolvedRequest),
};

type BadgeTone = "neutral" | "accent" | "success" | "warning" | "danger" | "muted";

const IMPACT_TONE: Record<string, BadgeTone> = {
  DIRECT: "success",
  POTENTIAL: "accent",
  TESTS: "warning",
  EXTERNAL: "muted",
  UNRESOLVED: "warning",
  REQUIRED: "danger",
  RECOMMENDED: "warning",
  INFORMATIONAL: "neutral",
};

const KIND_LABEL: Record<string, string> = {
  "changed-symbol": "Changed symbol",
  "changed-file": "Changed file",
  "diff-hunk": "Hunk",
  "potentially-affected": "Potentially affected",
  test: "Test",
  external: "External",
  "unresolved-call": "Unresolved call",
  "review-item": "Checklist item",
  "review-entry": "Checklist entry",
  "impact-path": "Call chain",
  warning: "Note",
};

export function LensPanel({
  open,
  onClose,
  repositoryId,
  kind,
  title,
  description,
  body,
  onOpenEvidence,
}: LensPanelProps) {
  const reduce = useReducedMotion();
  const [data, setData] = useState<LensResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const panelRef = useRef<HTMLDivElement>(null);
  const inflight = useRef(false);

  const run = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    setLoading(true);
    setFailed(null);
    setUnavailable(false);
    setData(null);
    try {
      const result = await RUNNERS[kind](repositoryId, body);
      setData(result);
    } catch (err: unknown) {
      const state = lensErrorState(err);
      if (state.unavailable) setUnavailable(true);
      else setFailed(state.message);
    } finally {
      inflight.current = false;
      setLoading(false);
    }
  }, [kind, repositoryId, body]);

  useEffect(() => {
    if (!open) return;
    setNonce((n) => n + 1);
  }, [open]);

  useEffect(() => {
    if (open && nonce > 0) void run();
  }, [open, nonce, run]);

  // Esc closes the drawer; focus is trapped on the panel.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    panelRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50"
          role="dialog"
          aria-modal="true"
          aria-label={title}
          initial={reduce ? { opacity: 1 } : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
        >
          {/* Backdrop */}
          <button
            type="button"
            aria-label="Close Lens"
            onClick={onClose}
            className="absolute inset-0 h-full w-full cursor-default bg-black/50"
          />

          {/* Drawer */}
          <motion.div
            ref={panelRef}
            tabIndex={-1}
            className="absolute right-0 top-0 flex h-full w-[420px] max-w-[92vw] flex-col border-l border-line bg-ink-900 shadow-panel outline-none"
            initial={reduce ? { x: 0 } : { x: 420 }}
            animate={{ x: 0 }}
            exit={reduce ? { opacity: 0 } : { x: 420 }}
            transition={{ type: "tween", duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
          >
            {/* Header */}
            <div className="flex shrink-0 items-start gap-3 border-b border-line px-4 py-3">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-accent/40 bg-accent/10 text-accent-hover">
                <Sparkles className="h-4 w-4" />
              </span>
              <div className="min-w-0 flex-1">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-text-primary">
                  {title}
                  <Badge tone="accent">Lens</Badge>
                </h2>
                <p className="mt-0.5 text-[11px] leading-relaxed text-text-muted">
                  {description}
                </p>
                {data && (
                  <p className="mt-1 font-mono text-[10px] text-text-faint">
                    {data.provider} · {data.model} · v{data.prompt_version}
                  </p>
                )}
              </div>
              <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close">
                <X className="h-4 w-4" />
              </Button>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto px-4 py-4">
              {loading && (
                <div className="flex flex-col items-center gap-3 py-10 text-center">
                  <Loader2 className="h-6 w-6 animate-spin text-accent-hover" />
                  <p className="text-sm text-text-secondary">
                    Running Lens… synthesizing an explanation from verified
                    findings.
                  </p>
                  <p className="text-[11px] text-text-faint">
                    The model only explains RepoLens' deterministic analysis; it
                    never creates or decides findings.
                  </p>
                </div>
              )}

              {!loading && unavailable && (
                <div className="flex flex-col gap-3 py-8">
                  <div className="flex items-start gap-3 rounded-md border border-line bg-ink-800 px-4 py-3">
                    <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-text-muted" />
                    <div className="text-sm leading-relaxed text-text-secondary">
                      AI explanations are optional. Deterministic analysis —
                      diff, impact, review, and exploration — is fully
                      available without Lens.
                      <p className="mt-1 text-xs text-text-faint">
                        Lens needs an LLM provider to be configured. Nothing
                        else in RepoLens changes.
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {!loading && failed && (
                <div className="flex flex-col gap-3 py-8">
                  <div className="flex items-start gap-3 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span className="leading-relaxed">{failed}</span>
                  </div>
                  <Button variant="secondary" size="sm" onClick={() => void run()}>
                    <RefreshCw className="h-3.5 w-3.5" />
                    Try again
                  </Button>
                </div>
              )}

              {!loading && !unavailable && !failed && data && (
                <LensResult data={data} onOpenEvidence={onOpenEvidence} />
              )}
            </div>

            {/* Footer */}
            {data && !loading && (
              <div className="flex shrink-0 items-center gap-2 border-t border-line px-4 py-2 text-[10px] text-text-faint">
                <CheckSquare className="h-3 w-3 text-success" />
                Evidence is verified by RepoLens' deterministic analysis; Lens
                only annotates it.
              </div>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function LensResult({
  data,
  onOpenEvidence,
}: {
  data: LensResponse;
  onOpenEvidence?: (evidence: LensEvidenceInfo) => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <section>
        <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-text-faint">
          Summary
        </h3>
        <p className="rounded-md border border-line bg-ink-800 px-3 py-2.5 text-[13px] leading-relaxed text-text-primary">
          {data.summary}
        </p>
      </section>

      {data.evidence.length > 0 && (
        <section>
          <h3 className="mb-1.5 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-text-faint">
            Evidence the explanation cites
            <Badge tone="muted">{data.evidence.length}</Badge>
          </h3>
          <div className="flex flex-col gap-2">
            {data.evidence.map((evidence) => (
              <EvidenceCard
                key={`${evidence.kind}-${evidence.label}-${evidence.index}`}
                evidence={evidence}
                onOpenEvidence={onOpenEvidence}
              />
            ))}
          </div>
        </section>
      )}

      {data.suggested_checks.length > 0 && (
        <section>
          <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-text-faint">
            Suggested checks
          </h3>
          <ul className="flex flex-col gap-1.5">
            {data.suggested_checks.map((check, i) => (
              <li
                key={i}
                className="flex items-start gap-2 rounded-md border border-line bg-ink-800/60 px-3 py-2 text-[12px] leading-relaxed text-text-secondary"
              >
                <CheckSquare className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent-hover" />
                <span>{check}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.uncertainty && (
        <section className="rounded-md border border-warning/25 bg-warning/5 px-3 py-2.5">
          <h3 className="mb-1 text-[11px] font-medium uppercase tracking-wider text-warning">
            Uncertainty
          </h3>
          <p className="text-[12px] leading-relaxed text-text-secondary">
            {data.uncertainty}
          </p>
        </section>
      )}
    </div>
  );
}

function EvidenceCard({
  evidence,
  onOpenEvidence,
}: {
  evidence: LensEvidenceInfo;
  onOpenEvidence?: (evidence: LensEvidenceInfo) => void;
}) {
  const tone = IMPACT_TONE[evidence.impact ?? ""] ?? "neutral";
  const canOpen = onOpenEvidence && (evidence.file || evidence.symbol_id != null);
  return (
    <div className="rounded-md border border-line bg-ink-800/80">
      <div className="flex items-center gap-2 px-3 py-2">
        {evidence.impact && <Badge tone={tone}>{evidence.impact}</Badge>}
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-text-primary">
          {evidence.label}
        </span>
        <Badge tone="muted">{KIND_LABEL[evidence.kind] ?? evidence.kind}</Badge>
      </div>
      {(evidence.file || evidence.line) && (
        <div className="flex items-center gap-2 border-t border-line/60 bg-ink-900/60 px-3 py-1.5">
          <FileCode2 className="h-3 w-3 shrink-0 text-text-faint" />
          <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-text-secondary">
            {evidence.file}
            {evidence.line ? `:${evidence.line}` : ""}
          </span>
          {canOpen && (
            <button
              type="button"
              onClick={() => onOpenEvidence?.(evidence)}
              className="shrink-0 text-[10px] font-medium text-accent-hover transition-colors hover:text-accent"
            >
              Open →
            </button>
          )}
        </div>
      )}
      {evidence.detail && (
        <pre
          className={cn(
            "overflow-x-auto whitespace-pre-wrap break-words border-t border-line/60 px-3 py-1.5 font-mono text-[10px] leading-relaxed text-text-muted"
          )}
        >
          {evidence.detail}
        </pre>
      )}
      {evidence.snippet && (
        <pre
          className={cn(
            "overflow-x-auto whitespace-pre bg-ink-900 px-3 py-2 font-mono text-[10px] leading-relaxed text-text-secondary"
          )}
        >
          {evidence.snippet}
        </pre>
      )}
    </div>
  );
}