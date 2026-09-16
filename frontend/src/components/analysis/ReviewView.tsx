import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  ExternalLink,
  FileCode2,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import {
  createReview,
  getReview,
  updateReviewEntry,
  updateReviewItem,
} from "../../api/client";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { LensPanel } from "../lens/LensPanel";
import type {
  ReviewEntryInfo,
  ReviewInfo,
  ReviewItemInfo,
  ReviewItemStatus,
} from "../../types";

interface ReviewViewProps {
  repositoryId: number;
  reviewId?: number | null;
  analysisId?: number | null;
  onReviewCreated: (reviewId: number) => void;
}

type FilterKey =
  | "all"
  | "open"
  | "done"
  | "required"
  | "recommended"
  | "tests"
  | "unresolved"
  | "external";

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: "all", label: "All" },
  { key: "open", label: "Open" },
  { key: "done", label: "Done" },
  { key: "required", label: "Required" },
  { key: "recommended", label: "Recommended" },
  { key: "tests", label: "Tests" },
  { key: "unresolved", label: "Unresolved" },
  { key: "external", label: "External" },
];

const PRIORITY_TONE: Record<string, "danger" | "warning" | "muted"> = {
  REQUIRED: "danger",
  RECOMMENDED: "warning",
  INFORMATIONAL: "muted",
};

const TYPE_LABEL: Record<string, string> = {
  CHANGED_CODE: "Changed code",
  AFFECTED_CALLER: "Affected callers",
  AFFECTED_DEPENDENCY: "Affected dependencies",
  AFFECTED_TEST: "Tests",
  UNRESOLVED_IMPACT: "Unresolved impact",
  EXTERNAL_DEPENDENCY: "External usage",
  CONFIGURATION_CHANGE: "Config change",
  DOCUMENTATION_CHANGE: "Docs",
};

const STATUSES: ReviewItemStatus[] = ["OPEN", "IN_PROGRESS", "DONE", "SKIPPED"];

export function ReviewView({
  repositoryId,
  reviewId,
  analysisId,
  onReviewCreated,
}: ReviewViewProps) {
  const navigate = useNavigate();
  const [review, setReview] = useState<ReviewInfo | null>(null);
  const [loading, setLoading] = useState<boolean>(!!reviewId);
  const [creating, setCreating] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [lensOpen, setLensOpen] = useState(false);

  const openLensEvidence = useCallback(
    (evidence: { file?: string | null; line?: number | null }) => {
      if (!evidence.file) return;
      const params = new URLSearchParams({ path: evidence.file });
      if (evidence.line) params.set("line", String(evidence.line));
      navigate(`/repo/${repositoryId}/source?${params.toString()}`);
    },
    [repositoryId, navigate]
  );

  const load = useCallback(
    async (id: number) => {
      setLoading(true);
      setError(null);
      try {
        const data = await getReview(repositoryId, id);
        setReview(data);
      } catch (err: unknown) {
        setError(
          (err as { response?: { data?: { detail?: string } } })?.response?.data
            ?.detail ?? "Failed to load review."
        );
      } finally {
        setLoading(false);
      }
    },
    [repositoryId]
  );

  useEffect(() => {
    if (reviewId) void load(reviewId);
  }, [reviewId, load]);

  const handleCreate = useCallback(async () => {
    if (!analysisId) return;
    setCreating(true);
    setError(null);
    try {
      const created = await createReview(repositoryId, analysisId);
      setReview(created);
      onReviewCreated(created.review_id);
    } catch (err: unknown) {
      setError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Failed to create review."
      );
    } finally {
      setCreating(false);
    }
  }, [repositoryId, analysisId, onReviewCreated]);

  const handleRegenerate = useCallback(async () => {
    if (!analysisId) return;
    setRegenerating(true);
    setError(null);
    try {
      const fresh = await createReview(repositoryId, analysisId, true);
      setReview(fresh);
      onReviewCreated(fresh.review_id);
    } catch (err: unknown) {
      setError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Failed to regenerate review."
      );
    } finally {
      setRegenerating(false);
    }
  }, [repositoryId, analysisId, onReviewCreated]);

  const applyItemPatch = useCallback(
    async (itemId: number, patch: { status?: ReviewItemStatus; notes?: string }) => {
      if (!review) return;
      try {
        const updated = await updateReviewItem(repositoryId, review.review_id, itemId, patch);
        setReview((prev) =>
          prev
            ? {
                ...prev,
                items: prev.items.map((i) =>
                  i.item_id === itemId ? updated : i
                ),
              }
            : prev
        );
      } catch {
        setError("Failed to update review item.");
      }
    },
    [review, repositoryId]
  );

  const applyEntryPatch = useCallback(
    async (itemId: number, entryId: number, status: "OPEN" | "DONE") => {
      if (!review) return;
      try {
        const updated = await updateReviewEntry(
          repositoryId,
          review.review_id,
          itemId,
          entryId,
          status
        );
        setReview((prev) =>
          prev
            ? {
                ...prev,
                items: prev.items.map((i) =>
                  i.item_id === itemId
                    ? {
                        ...i,
                        entries: i.entries.map((e) =>
                          e.entry_id === entryId ? updated : e
                        ),
                      }
                    : i
                ),
              }
            : prev
        );
      } catch {
        setError("Failed to update review entry.");
      }
    },
    [review, repositoryId]
  );

  const filtered = useMemo(() => {
    if (!review) return [];
    const items = [...review.items];
    switch (filter) {
      case "open":
        return items.filter((i) => i.status === "OPEN");
      case "done":
        return items.filter((i) => i.status === "DONE");
      case "required":
        return items.filter((i) => i.priority === "REQUIRED");
      case "recommended":
        return items.filter((i) => i.priority === "RECOMMENDED");
      case "tests":
        return items.filter((i) => i.item_type === "AFFECTED_TEST");
      case "unresolved":
        return items.filter((i) => i.item_type === "UNRESOLVED_IMPACT");
      case "external":
        return items.filter((i) => i.item_type === "EXTERNAL_DEPENDENCY");
      default:
        return items;
    }
  }, [review, filter]);

  const grouped = useMemo(() => {
    const order: Record<string, number> = {
      REQUIRED: 0,
      RECOMMENDED: 1,
      INFORMATIONAL: 2,
    };
    return [...filtered].sort(
      (a, b) => order[a.priority] - order[b.priority]
    );
  }, [filtered]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState
          icon={<Loader2 className="h-8 w-8 animate-spin text-accent-hover" />}
          title="Loading review…"
          description="Fetching the deterministic change review checklist."
        />
      </div>
    );
  }

  if (!review && !analysisId) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState
          icon={<ClipboardCheck className="h-8 w-8 text-text-faint" />}
          title="No review selected"
          description="Run an impact simulation and create a review from its findings."
        />
      </div>
    );
  }

  if (!review && analysisId) {
    return (
      <div className="flex flex-1 flex-col">
        {error && (
          <div className="flex items-start gap-2 border-b border-danger/20 bg-danger/10 px-4 py-2 text-sm text-danger">
            {error}
          </div>
        )}
        <div className="flex flex-1 items-center justify-center">
          <EmptyState
            icon={<ClipboardCheck className="h-8 w-8 text-text-faint" />}
            title="No review yet"
            description="Create a deterministic review checklist from the impact analysis findings."
            action={
              <Button
                variant="primary"
                isLoading={creating}
                onClick={() => void handleCreate()}
              >
                Create review
              </Button>
            }
          />
        </div>
      </div>
    );
  }

  if (!review) return null;

  const summary = review.review_summary;

  return (
    <div className="flex h-full flex-col">
      {error && (
        <div className="flex items-start gap-2 border-b border-danger/20 bg-danger/10 px-4 py-2 text-sm text-danger">
          {error}
        </div>
      )}

      {/* Header */}
      <div className="shrink-0 border-b border-line bg-ink-900 p-3">
        <div className="flex items-center gap-2">
          <ClipboardCheck className="h-4 w-4 text-accent-hover" />
          <span className="text-sm font-medium text-text-primary">
            Before you merge
          </span>
          <span className="font-mono text-xs text-text-faint">
            {review.base_revision.slice(0, 12)}
          </span>
          <ArrowRight className="h-3 w-3 text-text-faint" />
          <span className="font-mono text-xs text-text-faint">
            {review.head_revision.slice(0, 12)}
          </span>
          <Badge tone="neutral">depth {review.max_depth}</Badge>
          <Badge tone={review.status === "DONE" ? "success" : "neutral"}>
            {review.status}
          </Badge>
          <div className="ml-auto flex items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setLensOpen(true)}
            >
              <Sparkles className="h-3.5 w-3.5" />
              Explain this review
            </Button>
            <Button
              size="sm"
              variant="secondary"
              isLoading={regenerating}
              onClick={() => void handleRegenerate()}
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Regenerate
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                const el = document.getElementById("review-summary");
                el?.scrollIntoView({ behavior: "smooth" });
              }}
            >
              Why this review?
            </Button>
          </div>
        </div>
        <p className="mt-2 text-[11px] text-text-faint">
          A checklist built deterministically from the verified diff and impact
          findings for this change. No LLM decides checklist content or
          priority; checking these items does not guarantee runtime impact or
          correctness.
        </p>
      </div>

      {/* Scorecard + filters */}
      <div className="flex shrink-0 items-center gap-2 border-b border-line bg-ink-900/60 px-4 py-2">
        <ScoreTile
          label="Required"
          value={summary.required}
          className="border-danger/30 text-danger"
        />
        <ScoreTile
          label="Recommended"
          value={summary.recommended}
          className="border-warning/30 text-warning"
        />
        <ScoreTile
          label="Informational"
          value={summary.informational}
          className="border-line text-text-faint"
        />
        <ScoreTile
          label="Completed"
          value={summary.completed}
          className="border-success/30 text-success"
        />
        <div className="ml-2 flex items-center gap-2">
          <div className="h-1.5 w-28 overflow-hidden rounded-full bg-ink-800">
            <div
              className="h-full rounded-full bg-success transition-all duration-500"
              style={{
                width:
                  summary.total_items > 0
                    ? `${Math.round((summary.completed / summary.total_items) * 100)}%`
                    : "0%",
              }}
            />
          </div>
          <span className="whitespace-nowrap font-mono text-[11px] text-text-muted">
            {summary.completed} / {summary.total_items} complete
          </span>
        </div>
        <div className="ml-2 h-6 w-px bg-line" />
        <div className="flex flex-wrap items-center gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={cn(
                "rounded-md border px-2 py-1 text-[11px] font-medium transition-colors",
                filter === f.key
                  ? "border-accent bg-accent/15 text-accent-hover"
                  : "border-line bg-ink-800 text-text-secondary hover:border-ink-300"
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Items */}
      <div className="flex-1 overflow-auto">
        <div id="review-summary" className="mx-auto max-w-3xl flex flex-col gap-4 p-4">
          <div className="rounded-md border border-line bg-ink-900 px-4 py-3">
            <p className="text-xs leading-relaxed text-text-muted">
              {review.summary}
            </p>
          </div>
          {grouped.length === 0 && (
            <p className="py-10 text-center text-sm text-text-muted">
              No items match this filter.
            </p>
          )}
          {grouped.map((item) => (
            <ReviewItemCard
              key={item.item_id}
              repositoryId={repositoryId}
              item={item}
              onPatch={applyItemPatch}
              onEntryPatch={applyEntryPatch}
            />
          ))}
        </div>
      </div>

      <LensPanel
        open={lensOpen}
        onClose={() => setLensOpen(false)}
        repositoryId={repositoryId}
        kind="review"
        title="Why should I review this?"
        description={
          review
            ? `Lens annotates the ${summary.total_items} deterministic checklist items grouped by priority with a narrative of what is at risk and why.`
            : "Lens annotates the deterministic review checklist with a narrative of what is at risk and why."
        }
        body={{ review_id: review?.review_id ?? 0 }}
        onOpenEvidence={openLensEvidence}
      />
    </div>
  );
}

function ScoreTile({
  label,
  value,
  className,
}: {
  label: string;
  value: number;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-md border bg-ink-800 px-3 py-1.5 text-center",
        className
      )}
    >
      <div className="font-mono text-sm font-semibold">{value}</div>
      <div className="text-[10px] uppercase tracking-wider opacity-80">
        {label}
      </div>
    </div>
  );
}

function ReviewItemCard({
  repositoryId,
  item,
  onPatch,
  onEntryPatch,
}: {
  repositoryId: number;
  item: ReviewItemInfo;
  onPatch: (itemId: number, patch: { status?: ReviewItemStatus; notes?: string }) => void;
  onEntryPatch: (itemId: number, entryId: number, status: "OPEN" | "DONE") => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [notes, setNotes] = useState(item.notes);

  return (
    <article className="overflow-hidden rounded-md border border-line bg-ink-900">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-ink-800"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-text-faint" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-text-faint" />
        )}
        <Badge tone={PRIORITY_TONE[item.priority]}>{item.priority}</Badge>
        <Badge tone="neutral">{TYPE_LABEL[item.item_type] ?? item.item_type}</Badge>
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-text-primary">
          {item.title}
        </span>
        {item.evidence_file && (
          <span className="shrink-0 font-mono text-[10px] text-text-faint">
            {shortPath(item.evidence_file)}:{item.evidence_start_line}
          </span>
        )}
        <span
          className={cn(
            "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
            item.status === "DONE"
              ? "bg-success/12 text-success"
              : item.status === "IN_PROGRESS"
                ? "bg-warning/12 text-warning"
                : item.status === "SKIPPED"
                  ? "bg-ink-600 text-text-faint"
                  : "bg-ink-600 text-text-secondary"
          )}
        >
          {item.status}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-line/60 px-4 py-3">
          <p className="text-xs leading-relaxed text-text-secondary">
            {item.description}
          </p>

          {item.entries.length > 0 && (
            <div className="mt-3 flex flex-col gap-1.5">
              <p className="text-[10px] font-medium uppercase tracking-wider text-text-faint">
                Checklist
              </p>
              {item.entries.map((entry) => (
                <EntryRow
                  key={entry.entry_id}
                  repositoryId={repositoryId}
                  itemId={item.item_id}
                  entry={entry}
                  isTest={item.item_type === "AFFECTED_TEST"}
                  onEntryPatch={onEntryPatch}
                />
              ))}
            </div>
          )}

          <div className="mt-3 flex flex-col gap-1.5">
            <p className="text-[10px] font-medium uppercase tracking-wider text-text-faint">
              Status &amp; notes
            </p>
            <div className="flex flex-wrap gap-1.5">
              {STATUSES.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => onPatch(item.item_id, { status: s })}
                  className={cn(
                    "rounded-md border px-2 py-1 text-[11px] font-medium transition-colors",
                    item.status === s
                      ? "border-accent bg-accent/15 text-accent-hover"
                      : "border-line bg-ink-800 text-text-secondary hover:border-ink-300"
                  )}
                >
                  {s}
                </button>
              ))}
            </div>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              onBlur={() => {
                if (notes !== item.notes) onPatch(item.item_id, { notes });
              }}
              rows={2}
              placeholder="Add a note for this item…"
              className="mt-1 w-full resize-none rounded-md border border-line bg-ink-800 px-3 py-2 font-mono text-xs text-text-primary placeholder:text-text-faint focus:border-accent focus:outline-none"
            />
          </div>
        </div>
      )}
    </article>
  );
}

function EntryRow({
  repositoryId,
  itemId,
  entry,
  isTest,
  onEntryPatch,
}: {
  repositoryId: number;
  itemId: number;
  entry: ReviewEntryInfo;
  isTest: boolean;
  onEntryPatch: (itemId: number, entryId: number, status: "OPEN" | "DONE") => void;
}) {
  const [showSteps, setShowSteps] = useState(false);
  const kind = entry.kind || "FILE";
  return (
    <div
      className={cn(
        "rounded-md border bg-ink-800 px-3 py-2",
        isTest && entry.status === "DONE"
          ? "border-success/25"
          : "border-line"
      )}
    >
      <div className="flex items-start gap-2">
        {isTest && (
          <button
            type="button"
            aria-label="Toggle test checklist item"
            onClick={() =>
              onEntryPatch(
                itemId,
                entry.entry_id,
                entry.status === "DONE" ? "OPEN" : "DONE"
              )
            }
            className={cn(
              "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-colors",
              entry.status === "DONE"
                ? "border-success bg-success/25 text-success"
                : "border-ink-300 bg-ink-700 text-transparent hover:border-accent"
            )}
          >
            <CheckSquare className="h-3 w-3" />
          </button>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            {/* keep "kind" computed for the icon only; text is React-escaped */}
            {kind === "TEST" && (
              <Badge tone="warning">test</Badge>
            )}
            {kind === "UNRESOLVED" && (
              <AlertTriangle className="h-3.5 w-3.5 text-danger" />
            )}
            {kind === "EXTERNAL" && (
              <ExternalLink className="h-3.5 w-3.5 text-text-muted" />
            )}
            {kind === "FILE" && <FileCode2 className="h-3.5 w-3.5 text-accent-hover" />}
            {kind === "CALLER" && (
              <ArrowRight className="h-3.5 w-3.5 text-accent-hover" />
            )}
            <span className="truncate font-mono text-[12px] font-medium text-text-primary">
              {entry.title}
            </span>
            {entry.evidence_file && (
              <span className="font-mono text-[10px] text-text-faint">
                {shortPath(entry.evidence_file)}:{entry.evidence_line}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-[11px] leading-relaxed text-text-muted">
            {entry.description}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {entry.symbol_id && (
              <Link
                to={`/repo/${repositoryId}/investigate?symbol=${entry.symbol_id}`}
                className="rounded border border-line px-1.5 py-0.5 text-[10px] text-accent-hover transition-colors hover:border-accent/40"
              >
                Explore
              </Link>
            )}
            {entry.file_path && (
              <Link
                to={`/repo/${repositoryId}/investigate?file=${encodeURIComponent(entry.file_path)}`}
                className="rounded border border-line px-1.5 py-0.5 text-[10px] text-text-secondary transition-colors hover:border-accent/40 hover:text-text-primary"
              >
                Open source
              </Link>
            )}
            {entry.path_steps.length > 0 && (
              <button
                type="button"
                onClick={() => setShowSteps((v) => !v)}
                className="rounded border border-line px-1.5 py-0.5 text-[10px] text-text-secondary transition-colors hover:border-accent/40 hover:text-text-primary"
              >
                {showSteps ? "Hide path" : "Show impact path"}
              </button>
            )}
          </div>
          {showSteps && entry.path_steps.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1">
              {entry.path_steps.map((step, i) => (
                <span key={i} className="flex items-center gap-1">
                  <span className="rounded bg-ink-700 px-1.5 py-0.5 font-mono text-[10px]">
                    <span className="text-text-primary">{step.source}</span>
                    <span className="mx-1 text-text-faint">{step.relationship}</span>
                    <span className="text-text-faint">{step.target}</span>
                  </span>
                  {i < entry.path_steps.length - 1 && (
                    <ArrowRight className="h-2.5 w-2.5 text-text-faint" />
                  )}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function shortPath(path: string): string {
  const parts = path.split("/");
  return parts.length > 2 ? parts.slice(-2).join("/") : path;
}