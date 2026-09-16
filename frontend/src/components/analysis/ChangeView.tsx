import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowRight,
  FileCode2,
  GitCompare,
  Loader2,
  Plus,
  Minus,
  Sparkles,
} from "lucide-react";
import {
  createDiff,
  getDiffDetail,
  getDiffFileHunks,
} from "../../api/client";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { EmptyState } from "../ui/EmptyState";
import { LensPanel } from "../lens/LensPanel";
import type {
  DiffDetail,
  DiffFileInfo,
  DiffFileStatus,
  DiffFileCategory,
  DiffHunkInfo,
  DiffSymbolInfo,
  DiffSymbolChangeType,
  LensChangeRequest,
} from "../../types";
import { EMPTY_TREE_SHA } from "../../types";

interface ChangeViewProps {
  repositoryId: number;
  defaultHead?: string;
}

type BadgeTone =
  | "neutral"
  | "accent"
  | "success"
  | "warning"
  | "danger"
  | "muted";

const STATUS_TONE: Record<DiffFileStatus, BadgeTone> = {
  ADDED: "success",
  MODIFIED: "accent",
  DELETED: "danger",
  RENAMED: "warning",
  COPIED: "warning",
  TYPE_CHANGED: "warning",
  UNTRACKED: "muted",
};

const CATEGORY_TONE: Record<DiffFileCategory, BadgeTone> = {
  SOURCE: "accent",
  TEST: "warning",
  CONFIG: "neutral",
  DOCUMENTATION: "muted",
  UNKNOWN: "muted",
};

const CHANGE_TONE: Record<DiffSymbolChangeType, BadgeTone> = {
  ADDED: "success",
  MODIFIED: "accent",
  DELETED: "danger",
};

export function ChangeView({
  repositoryId,
}: ChangeViewProps) {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"latest" | "custom">("latest");
  const [base, setBase] = useState("HEAD~1");
  const [head, setHead] = useState("HEAD");
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [diff, setDiff] = useState<DiffDetail | null>(null);

  const [selectedFile, setSelectedFile] = useState<DiffFileInfo | null>(null);
  const [hunks, setHunks] = useState<DiffHunkInfo[]>([]);
  const [hunksLoading, setHunksLoading] = useState(false);
  const [lensOpen, setLensOpen] = useState(false);

  const runAnalysis = useCallback(
    async (baseRev: string, headRev: string) => {
      setAnalyzing(true);
      setError(null);
      setDiff(null);
      try {
        const created = await createDiff(repositoryId, baseRev, headRev);
        const detail = await getDiffDetail(repositoryId, created.id);
        setDiff(detail);
        if (detail.files.length > 0) {
          setSelectedFile(detail.files[0]);
          setHunks([]);
        }
      } catch (err: unknown) {
        const detail = (err as { response?: { data?: { detail?: string } } })
          ?.response?.data?.detail;
        setError(detail ?? "Diff analysis failed.");
      } finally {
        setAnalyzing(false);
      }
    },
    [repositoryId]
  );

  // Default: the latest change in the repository (HEAD~1 → HEAD).
  useEffect(() => {
    void runAnalysis("HEAD~1", "HEAD");
  }, [runAnalysis]);

  useEffect(() => {
    if (!diff || !selectedFile) return;
    setHunksLoading(true);
    getDiffFileHunks(repositoryId, diff.id, selectedFile.id)
      .then((data) => setHunks(data.hunks ?? []))
      .catch(() => setHunks([]))
      .finally(() => setHunksLoading(false));
  }, [repositoryId, diff?.id, selectedFile?.id]);

  const openInvestigation = useCallback(
    (symbolId: number) => {
      navigate(`/repo/${repositoryId}/investigate?symbol=${symbolId}`);
    },
    [repositoryId, navigate]
  );

  const symbolsForFile = useCallback(
    (filePath: string): DiffSymbolInfo[] =>
      (diff?.symbols ?? []).filter((s) => s.file_path === filePath),
    [diff]
  );

  const lensBody = useCallback((): LensChangeRequest => {
    if (selectedFile) {
      return { diff_id: diff?.id ?? 0, diff_file_id: selectedFile.id };
    }
    return { diff_id: diff?.id ?? 0 };
  }, [diff?.id, selectedFile]);

  const openLensEvidence = useCallback(
    (evidence: { file?: string | null; line?: number | null }) => {
      if (!evidence.file) return;
      const params = new URLSearchParams({ path: evidence.file });
      if (evidence.line) params.set("line", String(evidence.line));
      navigate(`/repo/${repositoryId}/source?${params.toString()}`);
    },
    [repositoryId, navigate]
  );

  return (
    <div className="flex h-full flex-col">
      {/* Revision controls */}
      <div className="shrink-0 border-b border-line bg-ink-900 p-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2.5">
            <GitCompare className="h-4 w-4 text-accent-hover" />
            <div>
              <h2 className="text-sm font-semibold tracking-tight text-text-primary">
                What changed?
              </h2>
              <p className="text-[11px] text-text-faint">
                Start with the latest change, or compare any two versions.
              </p>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-1 rounded-md border border-line bg-ink-800 p-0.5">
            <button
              type="button"
              onClick={() => setMode("latest")}
              className={cn(
                "rounded px-2.5 py-1 text-xs font-medium transition-colors",
                mode === "latest"
                  ? "bg-accent/15 text-accent-hover"
                  : "text-text-secondary hover:text-text-primary"
              )}
            >
              Latest change
            </button>
            <button
              type="button"
              onClick={() => setMode("custom")}
              className={cn(
                "rounded px-2.5 py-1 text-xs font-medium transition-colors",
                mode === "custom"
                  ? "bg-accent/15 text-accent-hover"
                  : "text-text-secondary hover:text-text-primary"
              )}
            >
              Choose versions
            </button>
          </div>
        </div>

        {mode === "latest" ? (
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <span className="text-xs text-text-muted">
              Previous version:{" "}
              <span className="font-mono text-text-secondary">HEAD~1</span>{" "}
              <span className="text-text-faint">
                (the commit before the current one)
              </span>
            </span>
            <ArrowRight className="h-3 w-3 text-text-faint" />
            <span className="text-xs text-text-muted">
              Current version:{" "}
              <span className="font-mono text-text-secondary">HEAD</span>{" "}
              <span className="text-text-faint">(the latest commit)</span>
            </span>
            <Button
              size="sm"
              variant="primary"
              isLoading={analyzing}
              onClick={() => void runAnalysis("HEAD~1", "HEAD")}
              className="ml-1"
            >
              Show latest change
            </Button>
          </div>
        ) : (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Input
              className="h-8 w-56 font-mono text-xs"
              value={base}
              onChange={(e) => setBase(e.target.value)}
              placeholder="Previous version (SHA or ref)"
              spellCheck={false}
            />
            <ArrowRight className="h-3.5 w-3.5 text-text-faint" />
            <Input
              className="h-8 w-56 font-mono text-xs"
              value={head}
              onChange={(e) => setHead(e.target.value)}
              placeholder="Current version (SHA or ref)"
              spellCheck={false}
            />
            <Button
              size="sm"
              variant="primary"
              isLoading={analyzing}
              disabled={!base.trim() || !head.trim()}
              onClick={() => void runAnalysis(base.trim(), head.trim())}
            >
              Compare
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setBase(EMPTY_TREE_SHA);
                setHead("HEAD");
                void runAnalysis(EMPTY_TREE_SHA, "HEAD");
              }}
            >
              Full snapshot (from empty)
            </Button>
          </div>
        )}
        <p className="mt-2 text-[11px] text-text-faint">
          What changed between two revisions — deterministic, offline, based on
          Git history. Comparing against the empty tree shows the full snapshot.
        </p>
      </div>

      {error && (
        <div className="flex items-start gap-2 border-b border-danger/20 bg-danger/10 px-4 py-2 text-sm text-danger">
          <span className="font-medium">Diff failed:</span> {error}
        </div>
      )}

      {analyzing && !diff && (
        <div className="flex flex-1 items-center justify-center">
          <EmptyState
            icon={<Loader2 className="h-8 w-8 animate-spin text-accent-hover" />}
            title="Analyzing changes…"
            description="Running git diff and mapping symbols."
          />
        </div>
      )}

      {!analyzing && !diff && !error && (
        <div className="flex flex-1 items-center justify-center">
          <EmptyState
            icon={<GitCompare className="h-8 w-8 text-text-faint" />}
            title="No diff loaded"
            description="Enter base and head revisions to see what changed."
          />
        </div>
      )}

      {diff && !analyzing && (
        <>
          {/* Summary strip */}
          <div className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-1 border-b border-line bg-ink-900/60 px-4 py-2 text-xs text-text-secondary">
            <span className="font-mono text-text-faint">
              {diff.base_revision.slice(0, 12)}
            </span>
            <ArrowRight className="h-3 w-3 text-text-faint" />
            <span className="font-mono text-text-faint">
              {diff.head_revision.slice(0, 12)}
            </span>
            <span className="ml-2 text-text-primary">
              {diff.computed_at?.replace("T", " ").slice(0, 16)}
            </span>
            <span className="ml-auto flex items-center gap-4">
              <span className="flex items-center gap-1 font-mono">
                <FileCode2 className="h-3.5 w-3.5 text-text-muted" />
                {diff.files_changed} files
              </span>
              <span className="flex items-center gap-1 font-mono text-success">
                <Plus className="h-3.5 w-3.5" />
                {diff.insertions}
              </span>
              <span className="flex items-center gap-1 font-mono text-danger">
                <Minus className="h-3.5 w-3.5" />
                {diff.deletions}
              </span>
              <span className="flex items-center gap-1 font-mono text-accent-hover">
                {diff.symbols_changed} symbols changed
              </span>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setLensOpen(true)}
              >
                <Sparkles className="h-3.5 w-3.5" />
                Explain this change
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  navigate(
                    `/repo/${repositoryId}/impact?diff=${diff.id}&base=${diff.base_revision}&head=${diff.head_revision}`
                  )
                }
              >
                See what might be affected
              </Button>
            </span>
          </div>

          <div className="flex flex-1 overflow-hidden">
            {/* Changed files */}
            <div className="flex w-80 shrink-0 flex-col overflow-hidden border-r border-line bg-ink-900">
              <div className="border-b border-line px-4 py-2 text-[11px] font-medium uppercase tracking-wider text-text-faint">
                Changed files
              </div>
              <div className="flex-1 overflow-auto">
                {diff.files.map((file) => {
                  const syms = symbolsForFile(file.path);
                  return (
                    <button
                      key={`${file.status}-${file.path}`}
                      type="button"
                      onClick={() => {
                        setSelectedFile(file);
                        setHunks([]);
                      }}
                      className={cn(
                        "flex w-full items-start gap-2 border-b border-line/40 px-3 py-2.5 text-left transition-colors hover:bg-ink-700/40",
                        selectedFile?.id === file.id && "bg-ink-700/50"
                      )}
                    >
                      <span className="w-8 shrink-0 text-right font-mono text-[11px] text-text-faint">
                        <span className={cn(file.additions > 0 && "text-success")}>
                          +{file.additions}
                        </span>
                        <span className={cn(file.deletions > 0 && "text-danger")}>
                          -{file.deletions}
                        </span>
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <Badge tone={STATUS_TONE[file.status]}>
                            {file.status === "RENAMED" && file.old_path
                              ? `${file.old_path.split("/").pop()} → ${file.path.split("/").pop()}`
                              : file.status}
                          </Badge>
                          {file.binary && <Badge tone="muted">binary</Badge>}
                        </span>
                        <span className="mt-0.5 block truncate font-mono text-[12px] text-text-primary">
                          {file.status === "RENAMED" && file.old_path
                            ? file.path
                            : file.path}
                        </span>
                        {file.status === "RENAMED" && file.old_path && (
                          <span className="block truncate font-mono text-[11px] text-text-faint line-through">
                            {file.old_path}
                          </span>
                        )}
                        <span className="mt-0.5 flex items-center gap-1.5">
                          <Badge tone={CATEGORY_TONE[file.file_category]}>
                            {file.file_category}
                          </Badge>
                          {syms.length > 0 && (
                            <span className="font-mono text-[11px] text-accent-hover">
                              {syms.length} symbol{syms.length === 1 ? "" : "s"}
                            </span>
                          )}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* File detail: hunks + symbols */}
            <div className="flex-1 overflow-auto">
              {!selectedFile && (
                <div className="flex h-full items-center justify-center text-sm text-text-muted">
                  Select a changed file to inspect.
                </div>
              )}
              {selectedFile && (
                <FileDetail
                  file={selectedFile}
                  hunks={hunks}
                  symbols={symbolsForFile(selectedFile.path)}
                  loading={hunksLoading}
                  onOpenSymbol={openInvestigation}
                />
              )}
            </div>
          </div>
        </>
      )}

      <LensPanel
        open={lensOpen}
        onClose={() => setLensOpen(false)}
        repositoryId={repositoryId}
        kind="change"
        title="Why did this change?"
        description={
          selectedFile
            ? `Lens explains the changes to ${selectedFile.path} by annotating the deterministic diff findings for this file.`
            : "Lens explains the whole diff by annotating the deterministic findings for every changed file and symbol."
        }
        body={lensBody()}
        onOpenEvidence={openLensEvidence}
      />
    </div>
  );
}

function FileDetail({
  file,
  hunks,
  symbols,
  loading,
  onOpenSymbol,
}: {
  file: DiffFileInfo;
  hunks: DiffHunkInfo[];
  symbols: DiffSymbolInfo[];
  loading: boolean;
  onOpenSymbol: (symbolId: number) => void;
}) {
  return (
    <div className="flex flex-col">
      <div className="sticky top-0 z-10 border-b border-line bg-ink-900/95 px-4 py-2 backdrop-blur">
        <h3 className="font-mono text-sm text-text-primary">
          {file.status === "RENAMED" && file.old_path
            ? `${file.old_path} → ${file.path}`
            : file.path}
        </h3>
        <p className="mt-0.5 text-xs text-text-muted">
          +{file.additions} / -{file.deletions}
          {file.binary && " · binary file"}
        </p>
      </div>

      <div className="p-4">
        {symbols.length > 0 && (
          <section className="mb-4">
            <h4 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-text-faint">
              Affected symbols
            </h4>
            <div className="flex flex-col gap-1">
              {symbols.map((sym) => (
                <button
                  key={sym.id}
                  type="button"
                  onClick={() => onOpenSymbol(sym.symbol_id)}
                  className="flex items-center gap-2 rounded-md border border-line bg-ink-800 px-3 py-1.5 text-left transition-colors hover:border-accent/40 hover:bg-ink-700"
                >
                  <Badge tone={CHANGE_TONE[sym.change_type]}>
                    {sym.change_type}
                  </Badge>
                  <span className="font-mono text-[13px] text-text-primary">
                    {sym.symbol_name}
                  </span>
                  <Badge tone="muted">{sym.symbol_kind}</Badge>
                  <span className="ml-auto font-mono text-[11px] text-text-faint">
                    +{sym.added_lines} -{sym.deleted_lines}
                  </span>
                </button>
              ))}
            </div>
          </section>
        )}

        <section>
          <h4 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-text-faint">
            Hunks
          </h4>
          {loading && (
            <p className="text-sm text-text-muted">Loading hunks…</p>
          )}
          {!loading && hunks.length === 0 && !file.binary && (
            <p className="text-sm text-text-muted">
              No textual hunks for this file.
            </p>
          )}
          {!loading && file.binary && (
            <p className="text-sm text-text-muted">
              Binary file — content diff is not shown.
            </p>
          )}
          <div className="flex flex-col gap-2">
            {hunks.map((hunk) => (
              <HunkView key={hunk.id} hunk={hunk} />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function HunkView({ hunk }: { hunk: DiffHunkInfo }) {
  const lines = hunk.lines ?? [];
  return (
    <div className="overflow-hidden rounded-md border border-line bg-ink-900">
      <div className="border-b border-line bg-ink-800/60 px-3 py-1 font-mono text-[11px] text-text-muted">
        @@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count}{" "}
        {hunk.header}
      </div>
      <div className="max-h-96 overflow-auto">
        {lines.map((line, i) => (
          <div
            key={i}
            className={cn(
              "flex items-center gap-2 px-3 py-[1px] font-mono text-[12px]",
              line.side === "NEW"
                ? "bg-success/10 text-text-primary"
                : "bg-danger/10 text-text-muted"
            )}
          >
            <span className="w-8 shrink-0 text-right text-text-faint">
              {line.side === "NEW" ? line.line_number : ""}
            </span>
            <span className="w-8 shrink-0 text-right text-text-faint">
              {line.side === "OLD" ? line.line_number : ""}
            </span>
            <span className="w-4 shrink-0 text-center">
              {line.side === "NEW" ? (
                <Plus className="h-3 w-3 text-success" />
              ) : (
                <Minus className="h-3 w-3 text-danger" />
              )}
            </span>
            {line.text !== undefined && line.text !== null && (
              <span
                className={cn(
                  "flex-1 whitespace-pre overflow-x-auto",
                  line.side === "NEW"
                    ? "text-success/90"
                    : "text-danger/80"
                )}
              >
                {line.text.replace(/\^(\^\^)*/g, "")}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}