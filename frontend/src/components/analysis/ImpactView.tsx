import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowRight,
  ClipboardCheck,
  Loader2,
  Network,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import {
  createDiff,
  createReview,
  getDiffDetail,
  getImpactAnalysis,
  simulateImpact,
} from "../../api/client";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { EmptyState } from "../ui/EmptyState";
import { LensPanel } from "../lens/LensPanel";
import type {
  DiffDetail,
  ImpactAnalysisInfo,
  ImpactClass,
  ImpactNodeInfo,
  ImpactPathInfo,
} from "../../types";
import { EMPTY_TREE_SHA } from "../../types";

interface ImpactViewProps {
  repositoryId: number;
  initialDiffId?: number | null;
  initialBase?: string;
  initialHead?: string;
  initialAnalysisId?: number | null;
}

type BadgeTone =
  | "neutral"
  | "accent"
  | "success"
  | "warning"
  | "danger"
  | "muted";

const CLASS_TONE: Record<ImpactClass, BadgeTone> = {
  DIRECT: "success",
  POTENTIAL: "accent",
  TESTS: "warning",
  UNRESOLVED: "danger",
  EXTERNAL: "muted",
};

const CLASS_LABEL: Record<ImpactClass, string> = {
  DIRECT: "Directly affected",
  POTENTIAL: "May also be affected",
  TESTS: "Related tests",
  UNRESOLVED: "Unresolved",
  EXTERNAL: "External",
};

const CLASS_DOT: Record<ImpactClass, string> = {
  DIRECT: "bg-success",
  POTENTIAL: "bg-accent",
  TESTS: "bg-warning",
  UNRESOLVED: "bg-danger",
  EXTERNAL: "bg-ink-300",
};

const VIA_TONE: Record<string, BadgeTone> = {
  CALLS: "accent",
  IMPORTS: "neutral",
  REFERENCES: "success",
  EXTENDS: "warning",
  IMPLEMENTS: "warning",
  TESTS: "warning",
};

const NODE_W = 168;
const NODE_H = 34;
const COL_GAP = 14;
const ROW_H = 120;
const PAD = 16;

export function ImpactView({
  repositoryId,
  initialDiffId,
  initialBase,
  initialHead,
  initialAnalysisId,
}: ImpactViewProps) {
  const navigate = useNavigate();
  const isDefaultLatest =
    (initialBase ?? "") === "HEAD~1" && (initialHead ?? "HEAD") === "HEAD";
  const [mode, setMode] = useState<"latest" | "custom">(
    isDefaultLatest ? "latest" : "custom"
  );
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [base, setBase] = useState(initialBase ?? "HEAD~1");
  const [head, setHead] = useState(initialHead ?? "HEAD");
  const [depth, setDepth] = useState(2);
  const [showFiles, setShowFiles] = useState(true);
  const [running, setRunning] = useState(false);
  const [creatingReview, setCreatingReview] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<ImpactAnalysisInfo | null>(null);
  const [diff, setDiff] = useState<DiffDetail | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [lensOpen, setLensOpen] = useState<null | "impact" | "unresolved">(null);

  const openLensEvidence = useCallback(
    (evidence: { file?: string | null; line?: number | null }) => {
      if (!evidence.file) return;
      const params = new URLSearchParams({ path: evidence.file });
      if (evidence.line) params.set("line", String(evidence.line));
      navigate(`/repo/${repositoryId}/source?${params.toString()}`);
    },
    [repositoryId, navigate]
  );

  useEffect(() => {
    if (!initialAnalysisId) return;
    let active = true;
    getImpactAnalysis(repositoryId, initialAnalysisId)
      .then((info) => {
        if (!active) return;
        setAnalysis(info);
        setBase(info.base_revision);
        setHead(info.head_revision);
        setDepth(info.max_depth);
        setError(null);
      })
      .catch(() => {
        if (active) setError("Failed to load the existing analysis.");
      });
    return () => {
      active = false;
    };
  }, [repositoryId, initialAnalysisId]);

  const loadDiff = useCallback(
    async (diffId: number) => {
      try {
        const detail = await getDiffDetail(repositoryId, diffId);
        setDiff(detail);
        setBase(detail.base_revision);
        setHead(detail.head_revision);
      } catch {
        setDiff(null);
      }
    },
    [repositoryId]
  );

  useEffect(() => {
    if (initialDiffId) void loadDiff(initialDiffId);
  }, [initialDiffId, loadDiff]);

  const runSimulation = useCallback(async () => {
    setRunning(true);
    setError(null);
    setAnalysis(null);
    setSelected(null);
    try {
      let diffId = diff?.id ?? null;
      if (!diffId) {
        const created = await createDiff(repositoryId, base.trim(), head.trim());
        diffId = created.id;
      }
      const info = await simulateImpact(repositoryId, diffId, {
        max_depth: depth,
      });
      setAnalysis(info);
      if (!diff) {
        const detail = await getDiffDetail(repositoryId, diffId);
        setDiff(detail);
      }
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      setError(detail ?? "Impact simulation failed.");
    } finally {
      setRunning(false);
    }
  }, [repositoryId, base, head, depth, diff]);

  const createReviewAndGo = useCallback(async () => {
    if (!analysis) return;
    setCreatingReview(true);
    setError(null);
    try {
      const review = await createReview(repositoryId, analysis.analysis_id);
      navigate(
        `/repo/${repositoryId}/review?review=${review.review_id}` +
          `&analysis=${analysis.analysis_id}&diff=${analysis.diff_id}`
      );
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      setError(detail ?? "Failed to create a review.");
    } finally {
      setCreatingReview(false);
    }
  }, [analysis, repositoryId, navigate]);

  const onBaseChange = useCallback((value: string) => {
    setBase(value);
    setDiff(null);
  }, []);
  const onHeadChange = useCallback((value: string) => {
    setHead(value);
    setDiff(null);
  }, []);

  const nodes = useMemo(() => buildGraphNodes(analysis, depth, showFiles), [
    analysis,
    depth,
    showFiles,
  ]);

  const edges = useMemo(() => buildGraphEdges(analysis, nodes), [analysis, nodes]);

  const selectedPaths = useMemo(() => {
    if (!analysis || !selected) return [];
    return analysis.paths.filter(
      (p) => p.target === selected || p.root === selected
    );
  }, [analysis, selected]);

  const selectedNode = useMemo(() => {
    if (!analysis || !selected) return null;
    const all = [
      ...analysis.changed,
      ...analysis.potentially_affected,
      ...analysis.tests,
      ...analysis.unresolved,
      ...analysis.external,
    ];
    return all.find((n) => n.name === selected) ?? null;
  }, [analysis, selected]);

  return (
    <div className="flex h-full flex-col">
      {/* Revision controls */}
      <div className="shrink-0 border-b border-line bg-ink-900 p-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2.5">
            <Network className="h-4 w-4 text-accent-hover" />
            <div>
              <h2 className="text-sm font-semibold tracking-tight text-text-primary">
                What might be affected?
              </h2>
              <p className="text-[11px] text-text-faint">
                Trace how a change could ripple through the codebase from real
                relationships.
              </p>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-1 rounded-md border border-line bg-ink-800 p-0.5">
            <button
              type="button"
              onClick={() => {
                setMode("latest");
                onBaseChange("HEAD~1");
                onHeadChange("HEAD");
              }}
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
              isLoading={running}
              onClick={() => void runSimulation()}
              className="ml-1"
            >
              Simulate impact
            </Button>
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              className={cn(
                "ml-auto rounded-md border px-2 py-1 text-[11px] font-medium transition-colors",
                showAdvanced
                  ? "border-accent/40 bg-accent/10 text-accent-hover"
                  : "border-line bg-ink-800 text-text-secondary hover:border-ink-300"
              )}
            >
              {showAdvanced ? "Hide advanced options" : "Advanced options"}
            </button>
          </div>
        ) : (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Input
              className="h-8 w-52 font-mono text-xs"
              value={base}
              onChange={(e) => onBaseChange(e.target.value)}
              placeholder="Previous version (SHA or ref)"
              spellCheck={false}
            />
            <ArrowRight className="h-3.5 w-3.5 text-text-faint" />
            <Input
              className="h-8 w-52 font-mono text-xs"
              value={head}
              onChange={(e) => onHeadChange(e.target.value)}
              placeholder="Current version (SHA or ref)"
              spellCheck={false}
            />
            <Button
              size="sm"
              variant="primary"
              isLoading={running}
              disabled={!base.trim() || !head.trim()}
              onClick={() => void runSimulation()}
            >
              Simulate impact
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                onBaseChange(EMPTY_TREE_SHA);
                onHeadChange("HEAD");
              }}
            >
              Full snapshot (from empty)
            </Button>
          </div>
        )}

        {showAdvanced && mode === "latest" && (
          <div className="mt-2 flex flex-wrap items-center gap-4 rounded-md border border-line/60 bg-ink-800/60 px-3 py-2">
            <label className="flex items-center gap-1.5 text-xs text-text-muted">
              <span className="text-text-faint">Traversal depth</span>
              <select
                className="h-7 rounded-md border border-line bg-ink-700 px-2 text-xs text-text-primary"
                value={depth}
                onChange={(e) => setDepth(Number(e.target.value))}
              >
                <option value={1}>1</option>
                <option value={2}>2</option>
                <option value={3}>3</option>
                <option value={4}>4</option>
                <option value={5}>5</option>
                <option value={6}>6</option>
              </select>
            </label>
            <span className="text-[11px] text-text-faint">
              Default depth 2 — how many relationship hops to trace.
            </span>
          </div>
        )}
        <p className="mt-2 text-[11px] text-text-faint">
          Deterministic static impact simulation. It identifies potentially
          affected code based on observed repository relationships; it does not
          guarantee runtime impact or correctness.
        </p>
      </div>

      {error && (
        <div className="flex items-start gap-2 border-b border-danger/20 bg-danger/10 px-4 py-2 text-sm text-danger">
          <span className="font-medium">Simulation failed:</span> {error}
        </div>
      )}

      {running && !analysis && (
        <div className="flex flex-1 items-center justify-center">
          <EmptyState
            icon={<Loader2 className="h-8 w-8 animate-spin text-accent-hover" />}
            title="Simulating impact…"
            description="Traversing the relationship graph from changed entities."
          />
        </div>
      )}

      {!running && !analysis && !error && (
        <div className="flex flex-1 items-center justify-center">
          <EmptyState
            icon={<Network className="h-8 w-8 text-text-faint" />}
            title="No simulation yet"
            description="Choose revisions and run a simulation to see what is potentially affected."
          />
        </div>
      )}

      {analysis && (
        <>
          {/* Summary strip */}
          <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-1 border-b border-line bg-ink-900/60 px-4 py-2 text-xs text-text-secondary">
            <span className="font-mono text-text-faint">
              {analysis.base_revision.slice(0, 12)}
            </span>
            <ArrowRight className="h-3 w-3 text-text-faint" />
            <span className="font-mono text-text-faint">
              {analysis.head_revision.slice(0, 12)}
            </span>
            <span className="text-text-faint">depth {analysis.max_depth}</span>
            <span className="ml-auto flex items-center gap-4">
              <span className="font-mono text-success">
                {analysis.summary.changed_symbols} symbols
              </span>
              <span className="font-mono text-text-primary">
                {analysis.summary.changed_files} files
              </span>
              <span className="font-mono text-success">
                {analysis.summary.direct} directly affected
              </span>
              <span className="font-mono text-accent-hover">
                {analysis.summary.potential} may also be affected
              </span>
              <span className="font-mono text-warning">
                {analysis.summary.affected_tests} related tests
              </span>
              <span className="font-mono text-danger">
                {analysis.summary.unresolved} unresolved
              </span>
              <span className="font-mono text-text-muted">
                {analysis.summary.external} external
              </span>
              {analysis.summary.truncated && (
                <span className="flex items-center gap-1 font-mono text-warning">
                  <ShieldAlert className="h-3.5 w-3.5" />
                  truncated
                </span>
              )}
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setLensOpen("impact")}
              >
                <Sparkles className="h-3.5 w-3.5" />
                Why is this affected?
              </Button>
              <Button
                size="sm"
                variant="primary"
                isLoading={creatingReview}
                onClick={() => void createReviewAndGo()}
              >
                <ClipboardCheck className="h-3.5 w-3.5" />
                Review before merging
              </Button>
            </span>
          </div>

          <div className="flex flex-1 overflow-hidden">
            {/* Left: lists */}
            <div className="flex w-[26rem] shrink-0 flex-col overflow-hidden border-r border-line bg-ink-900">
              <div className="flex items-center gap-2 border-b border-line px-4 py-2">
                <span className="text-[11px] font-medium uppercase tracking-wider text-text-faint">
                  Impacted entities
                </span>
                <label className="ml-auto flex items-center gap-1.5 text-xs text-text-muted">
                  <input
                    type="checkbox"
                    checked={showFiles}
                    onChange={(e) => setShowFiles(e.target.checked)}
                  />
                  files
                </label>
              </div>
              <div className="flex-1 overflow-auto">
                <ImpactList
                  className="DIRECT"
                  nodes={analysis.changed}
                  selected={selected}
                  onSelect={setSelected}
                />
                <ImpactList
                  className="POTENTIAL"
                  nodes={analysis.potentially_affected}
                  showDepth
                  selected={selected}
                  onSelect={setSelected}
                />
                <ImpactList
                  className="TESTS"
                  nodes={analysis.tests}
                  selected={selected}
                  onSelect={setSelected}
                />
                <ImpactList
                  className="UNRESOLVED"
                  nodes={analysis.unresolved}
                  selected={selected}
                  onSelect={setSelected}
                  action={
                    <button
                      type="button"
                      onClick={() => setLensOpen("unresolved")}
                      className="flex items-center gap-1 rounded-md border border-line px-1.5 py-0.5 text-[10px] font-medium text-accent-hover transition-colors hover:border-accent/40 hover:bg-ink-700"
                    >
                      <Sparkles className="h-3 w-3" />
                      Explain
                    </button>
                  }
                />
                <ImpactList
                  className="EXTERNAL"
                  nodes={analysis.external}
                  selected={selected}
                  onSelect={setSelected}
                />
              </div>
            </div>

            {/* Right: graph + why panel */}
            <div className="flex flex-1 flex-col overflow-hidden">
              <div className="flex-1 overflow-auto">
                {nodes.length > 0 ? (
                  <ImpactGraph
                    nodes={nodes}
                    edges={edges}
                    selected={selected}
                    onSelect={setSelected}
                  />
                ) : (
                  <div className="flex h-full items-center justify-center text-sm text-text-muted">
                    No in-repository relationships reached within depth {depth}.
                  </div>
                )}
              </div>
              <div className="shrink-0 border-t border-line bg-ink-900/60 px-4 py-3">
                <WhyPanel
                  node={selectedNode}
                  paths={selectedPaths}
                  summary={analysis.summary}
                />
              </div>
            </div>
          </div>
        </>
      )}

      <LensPanel
        open={lensOpen !== null}
        onClose={() => setLensOpen(null)}
        repositoryId={repositoryId}
        kind={lensOpen ?? "impact"}
        title={lensOpen === "unresolved" ? "Why are these calls unresolved?" : "Why is this change impactful?"}
        description={
          lensOpen === "unresolved"
            ? "Lens annotates the unresolved external calls in this simulation with an explanation of what is missing and what to check."
            : "Lens annotates the deterministic impact findings — direct, potential, test, unresolved, and external — with a synthesis of what is affected and why."
        }
        body={{ analysis_id: analysis?.analysis_id ?? 0 }}
        onOpenEvidence={openLensEvidence}
      />
    </div>
  );
}

interface GraphNode {
  key: string;
  name: string;
  label: string;
  kind: string;
  layer: number;
  node: ImpactNodeInfo;
}

interface GraphEdge {
  from: string;
  to: string;
  rel: string;
}

function buildGraphNodes(
  analysis: ImpactAnalysisInfo | null,
  maxDepth: number,
  showFiles: boolean
): GraphNode[] {
  if (!analysis) return [];
  const result: GraphNode[] = [];
  const seen = new Set<string>();
  const push = (
    n: ImpactNodeInfo,
    layer: number,
    force: boolean = false
  ) => {
    if (!showFiles && n.node_type === "FILE" && !force) return;
    if (layer > maxDepth) return;
    const key = n.name;
    if (seen.has(key)) return;
    seen.add(key);
    result.push({
      key,
      name: n.name,
      label: displayName(n),
      kind: n.node_type === "SYMBOL" ? n.kind : "file",
      layer,
      node: n,
    });
  };
  for (const n of analysis.changed) push(n, 0, true);
  for (const n of analysis.tests) push(n, Math.max(1, n.depth));
  for (const n of analysis.potentially_affected) push(n, Math.max(1, n.depth));
  return result;
}

function buildGraphEdges(
  analysis: ImpactAnalysisInfo | null,
  nodes: GraphNode[]
): GraphEdge[] {
  if (!analysis) return [];
  const byName = new Map(nodes.map((n) => [n.name, n]));
  const edges: GraphEdge[] = [];
  const seen = new Set<string>();
  for (const path of analysis.paths) {
    const child = byName.get(path.target);
    if (!child || child.layer === 0) continue;
    const first = path.steps[0];
    const parent = first ? byName.get(first.target) : null;
    if (!parent || parent.layer >= child.layer) continue;
    const key = `${path.target}|${first.target}`;
    if (seen.has(key)) continue;
    seen.add(key);
    edges.push({ from: path.target, to: first.target, rel: first.relationship });
  }
  return edges;
}

function layoutGraph(
  nodes: GraphNode[]
): { width: number; height: number; coords: Map<string, { x: number; y: number }> } {
  const layers = new Map<number, GraphNode[]>();
  for (const n of nodes) {
    const arr = layers.get(n.layer) ?? [];
    arr.push(n);
    layers.set(n.layer, arr);
  }
  const coords = new Map<string, { x: number; y: number }>();
  let maxWidth = 0;
  for (const [layer, arr] of layers) {
    const y = PAD + layer * ROW_H;
    arr.forEach((node, col) => {
      coords.set(node.name, {
        x: PAD + col * (NODE_W + COL_GAP),
        y: y + col * 0,
      });
    });
    maxWidth = Math.max(maxWidth, PAD * 2 + arr.length * (NODE_W + COL_GAP) - COL_GAP);
  }
  const maxLayer = Math.max(0, ...layers.keys());
  const height = PAD * 2 + (maxLayer + 1) * ROW_H;
  return { width: Math.max(600, maxWidth), height, coords };
}

function displayName(n: ImpactNodeInfo): string {
  return n.name.split(".").pop() ?? n.name;
}

function ImpactGraph({
  nodes,
  edges,
  selected,
  onSelect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  const { width, height, coords } = layoutGraph(nodes);
  return (
    <div className="p-4">
      <svg
        width={width}
        height={height}
        className="rounded-md border border-line bg-ink-900"
        role="img"
        aria-label="Impact graph"
      >
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#3e4657" />
          </marker>
        </defs>
        {/* Edges */}
        {edges.map((edge) => {
          const from = coords.get(edge.from);
          const to = coords.get(edge.to);
          if (!from || !to) return null;
          const x1 = from.x + NODE_W;
          const y1 = from.y + NODE_H / 2;
          const x2 = to.x;
          const y2 = to.y + NODE_H / 2;
          const midY = (y1 + y2) / 2;
          const selectedEdge = selected === edge.from || selected === edge.to;
          return (
            <g key={`${edge.from}-${edge.to}`}>
              <path
                d={`M ${x1} ${y1} C ${x1 + 40} ${midY}, ${x2 - 40} ${midY}, ${x2} ${y2}`}
                fill="none"
                stroke={selectedEdge ? "#7aa7ff" : "#3e4657"}
                strokeWidth={selectedEdge ? 1.6 : 1}
                markerEnd="url(#arrow)"
              />
              <text
                x={(x1 + x2) / 2}
                y={midY - 6}
                textAnchor="middle"
                className="fill-[#7a8296]"
                fontSize={9}
                fontFamily="ui-monospace, monospace"
              >
                {edge.rel}
              </text>
            </g>
          );
        })}
        {/* Nodes */}
        {nodes.map((node) => {
          const pos = coords.get(node.name);
          if (!pos) return null;
          const isSel = selected === node.name;
          const tone =
            node.node.impact_class === "DIRECT"
              ? "fill-[#17a34a]/25 stroke-[#17a34a]/70"
              : node.node.impact_class === "TESTS"
                ? "fill-[#d97706]/20 stroke-[#d97706]/70"
                : "fill-[#4f8cff]/15 stroke-[#4f8cff]/60";
          return (
            <g
              key={node.key}
              onClick={() => onSelect(node.name)}
              className="cursor-pointer"
            >
              <rect
                x={pos.x}
                y={pos.y}
                width={NODE_W}
                height={NODE_H}
                rx={6}
                className={cn(
                  tone,
                  isSel && "stroke-[#7aa7ff] stroke-[2]"
                )}
                strokeWidth={1}
              />
              <text
                x={pos.x + 8}
                y={pos.y + 14}
                fontSize={10}
                fill="#cfd6e4"
                fontFamily="ui-monospace, monospace"
              >
                {node.label}
              </text>
              <text
                x={pos.x + 8}
                y={pos.y + 27}
                fontSize={8.5}
                fill="#7a8296"
                fontFamily="ui-monospace, monospace"
              >
                {node.node.impact_class === "DIRECT"
                  ? node.node.change_type ?? "changed"
                  : node.kind}
                {node.node.impact_class !== "DIRECT" && node.node.depth > 0
                  ? ` · d${node.node.depth}`
                  : ""}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function ImpactList({
  className,
  nodes,
  selected,
  onSelect,
  showDepth,
  action,
  title,
}: {
  className: ImpactClass;
  nodes: ImpactNodeInfo[];
  selected: string | null;
  onSelect: (name: string) => void;
  showDepth?: boolean;
  action?: ReactNode;
  title?: string;
}) {
  if (nodes.length === 0) return null;
  return (
    <section className="border-b border-line/60">
      <div className="flex items-center gap-2 px-4 py-2">
        <span
          className={cn("h-2 w-2 shrink-0 rounded-full", CLASS_DOT[className])}
          aria-hidden="true"
        />
        <span className="text-xs font-semibold text-text-secondary">
          {title ?? CLASS_LABEL[className]}
        </span>
        <span className="ml-auto font-mono text-[11px] text-text-faint">
          {nodes.length}
        </span>
        {action && <span className="flex items-center">{action}</span>}
      </div>
      <div className="px-2 pb-2">
        {nodes.map((n) => (
          <button
            key={`${className}-${n.name}`}
            type="button"
            onClick={() => onSelect(n.name)}
            className={cn(
              "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-ink-700/50",
              selected === n.name && "bg-ink-700/70"
            )}
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate font-mono text-[12px] text-text-primary">
                {displayName(n)}
              </span>
              <span className="block truncate font-mono text-[10px] text-text-faint">
                {n.name}
              </span>
            </span>
            {showDepth && n.depth > 0 && (
              <span className="font-mono text-[10px] text-text-faint">
                d{n.depth}
              </span>
            )}
            {n.via && (
              <span className="shrink-0">
                <Badge tone={VIA_TONE[n.via] ?? "neutral"}>{n.via}</Badge>
              </span>
            )}
          </button>
        ))}
      </div>
    </section>
  );
}

function WhyPanel({
  node,
  paths,
  summary,
}: {
  node: ImpactNodeInfo | null;
  paths: ImpactPathInfo[];
  summary: ImpactAnalysisInfo["summary"];
}) {
  if (!node) {
    return (
      <div className="text-xs text-text-muted">
        Select an entity to see <span className="text-text-primary">why it is affected</span>{" "}
        — relationship, evidence, and the path from a changed entity.
      </div>
    );
  }
  return (
    <div>
      <div className="flex items-center gap-2">
        <Badge tone={CLASS_TONE[node.impact_class]}>
          {CLASS_LABEL[node.impact_class]}
        </Badge>
        <span className="truncate font-mono text-xs text-text-primary">
          {node.name}
        </span>
        <span className="ml-auto font-mono text-[10px] text-text-faint">
          {node.evidence_file ? `${node.evidence_file}:${node.evidence_line}` : ""}
        </span>
      </div>
      <p className="mt-1.5 text-[11px] text-text-muted">
        {node.impact_class === "DIRECT" && (
          <>
            Changed directly by this diff
            {node.change_type ? ` (${node.change_type})` : ""}
            {node.is_file_level_change
              ? " · file-level change (no symbol mapping)"
              : ""}
            .
          </>
        )}
        {node.impact_class === "UNRESOLVED" && (
          <>
            Observed in <span className="text-text-secondary">{node.via_source}</span> from a
            changed entity at{" "}
            <span className="font-mono">
              {node.evidence_file}:{node.evidence_line}
            </span>{" "}
            — the receiver could not be resolved statically, so only the
            dependency is recorded.
          </>
        )}
        {node.impact_class === "EXTERNAL" && (
          <>
            Imported by a changed file at{" "}
            <span className="font-mono">
              {node.evidence_file}:{node.evidence_line}
            </span>{" "}
            from outside the repository.
          </>
        )}
        {(node.impact_class === "POTENTIAL" || node.impact_class === "TESTS") && (
          <>
            Reached via <span className="text-text-secondary">{node.via}</span>{" "}
            from <span className="font-mono">{node.evidence_file}:{node.evidence_line}</span>{" "}
            at depth {node.depth}.
          </>
        )}
      </p>

      {paths.length > 0 && (
        <div className="mt-2 flex flex-col gap-1.5">
          <p className="text-[10px] font-medium uppercase tracking-wider text-text-faint">
            Why is this affected?
          </p>
          {paths.map((path) => (
            <PathCard key={path.root + path.target} path={path} />
          ))}
        </div>
      )}
      {paths.length === 0 && summary.truncated && (
        <p className="mt-2 text-[11px] text-warning">
          Path data was not recorded after the node or path cap was reached.
        </p>
      )}
    </div>
  );
}

function PathCard({ path }: { path: ImpactPathInfo }) {
  return (
    <div className="overflow-hidden rounded-md border border-line bg-ink-800">
      {path.steps.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1 px-3 py-1.5">
          {path.steps.map((step, i) => (
            <span key={i} className="flex items-center gap-1">
              <span className="flex items-center gap-1 rounded bg-ink-700 px-1.5 py-0.5 font-mono text-[11px]">
                <span className="text-text-primary">{shortName(step.source)}</span>
                <Badge tone="neutral">{step.relationship}</Badge>
                <span className="text-text-faint">
                  {shortName(step.target)}
                </span>
              </span>
              {i < path.steps.length - 1 && (
                <ArrowRight className="h-3 w-3 text-text-faint" />
              )}
            </span>
          ))}
        </div>
      ) : (
        <div className="px-3 py-1.5 text-[11px] text-text-muted">
          {shortName(path.root)} is the root of this impact.
        </div>
      )}
      {path.steps[0] && (
        <div className="border-t border-line/60 px-3 py-1 font-mono text-[10px] text-text-faint">
          {path.steps[0].evidence}
        </div>
      )}
    </div>
  );
}

function shortName(name: string): string {
  return name.split(".").pop() ?? name;
}