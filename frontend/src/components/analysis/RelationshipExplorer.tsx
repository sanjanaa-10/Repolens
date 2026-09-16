import { Fragment, useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Network, RefreshCw } from "lucide-react";
import {
  buildRelationships,
  getRelationships,
} from "../../api/client";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Panel } from "../ui/Panel";
import type {
  RelationshipInfo,
  RelationshipSummary,
} from "../../types";

interface RelationshipExplorerProps {
  repositoryId: number;
  enabled: boolean;
}

type BadgeTone =
  | "neutral"
  | "accent"
  | "success"
  | "warning"
  | "danger"
  | "muted";

const TYPE_TONES: Record<string, BadgeTone> = {
  IMPORTS: "accent",
  EXPORTS: "accent",
  DEFINES: "neutral",
  CALLS: "success",
  REFERENCES: "warning",
  EXTENDS: "danger",
  IMPLEMENTS: "danger",
  TESTS: "muted",
};

const STATUS_TONES: Record<string, BadgeTone> = {
  RESOLVED: "success",
  UNRESOLVED: "warning",
  EXTERNAL: "muted",
};

const FILTERS = [
  { key: null, label: "All" },
  { key: "CALLS", label: "Calls" },
  { key: "IMPORTS", label: "Imports" },
  { key: "REFERENCES", label: "References" },
  { key: "EXTENDS", label: "Extends" },
  { key: "IMPLEMENTS", label: "Implements" },
  { key: "TESTS", label: "Tests" },
] as const;

function typeTone(type: string): BadgeTone {
  return TYPE_TONES[type] ?? "neutral";
}

function statusTone(status: string): BadgeTone {
  return STATUS_TONES[status] ?? "neutral";
}

function describe(rel: RelationshipInfo): { source: string; target: string } {
  const source = rel.source_symbol_name ?? rel.source_file ?? "file";
  const target = rel.target_symbol_name ?? rel.target_file ?? "unresolved";
  return { source, target };
}

export function RelationshipExplorer({
  repositoryId,
  enabled,
}: RelationshipExplorerProps) {
  const [built, setBuilt] = useState(false);
  const [summary, setSummary] = useState<RelationshipSummary | null>(null);
  const [relationships, setRelationships] = useState<RelationshipInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [building, setBuilding] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (showLoading = true) => {
      if (!enabled) return;
      if (showLoading) setLoading(true);
      setError(null);
      try {
        const data = await getRelationships(repositoryId, {
          type: filter ?? undefined,
          limit: 500,
        });
        setRelationships(data.items);
        setTotal(data.total);
        setBuilt(data.total > 0);
      } catch (err: unknown) {
        setRelationships([]);
        setTotal(0);
        setError(
          (err as { response?: { data?: { detail?: string } } })?.response
            ?.data?.detail ?? "Failed to load relationships."
        );
      } finally {
        if (showLoading) setLoading(false);
      }
    },
    [repositoryId, enabled, filter]
  );

  const build = useCallback(async () => {
    setBuilding(true);
    setError(null);
    try {
      const result = await buildRelationships(repositoryId);
      setSummary(result);
      setBuilt(true);
    } catch (err: unknown) {
      setError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Failed to build relationships."
      );
    } finally {
      setBuilding(false);
      void load(false);
    }
  }, [repositoryId, load]);

  useEffect(() => {
    if (enabled) void load();
  }, [enabled, filter, load]);

  const showEmpty = !loading && !error && (!built || total === 0);

  return (
    <Panel
      title="Relationship Explorer"
      subtitle="Deterministic graph of how symbols reference each other"
      actions={
        enabled ? (
          <Badge tone={built ? "accent" : "muted"}>
            {built ? `${total.toLocaleString()} relationships` : "Not built"}
          </Badge>
        ) : undefined
      }
    >
      {!enabled ? (
        <p className="text-sm text-text-muted">
          Run the analysis above to explore code relationships.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {!built && (
            <div className="flex flex-wrap items-center justify-between gap-4 rounded-md border border-line bg-ink-800 px-4 py-3">
              <p className="max-w-md text-sm text-text-muted">
                Relationships are derived deterministically from parsed symbols,
                imports, and source evidence — no code is executed.
              </p>
              <Button onClick={build} isLoading={building}>
                {building ? null : <Network className="h-4 w-4" />}
                {building ? "Building…" : "Build relationships"}
              </Button>
            </div>
          )}

          {summary && built && (
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="success">resolved {summary.resolved}</Badge>
              <Badge tone="muted">external {summary.external}</Badge>
              <Badge tone="warning">unresolved {summary.unresolved}</Badge>
              <span className="ml-auto inline-flex items-center gap-1 font-mono text-xs text-text-faint">
                <RefreshCw className="h-3 w-3" />
                {summary.duration_ms} ms
              </span>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-1">
            {FILTERS.map((item) => (
              <button
                key={item.key ?? "all"}
                className={`rounded-full px-2.5 py-1 text-xs font-medium transition-colors border ${
                  filter === item.key
                    ? "border-accent bg-accent/15 text-accent-hover"
                    : "border-line bg-ink-800 text-text-secondary hover:border-ink-300"
                }`}
                onClick={() => setFilter(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>

          {error && <p className="text-sm text-danger">{error}</p>}

          {loading && (
            <p className="py-4 text-center text-sm text-text-muted">
              Loading relationships…
            </p>
          )}

          {showEmpty && !loading && (
            <p className="py-4 text-center text-sm text-text-muted">
              No relationships found. Build the relationship index to populate
              this view.
            </p>
          )}

          {!loading && relationships.length > 0 && (
            <div className="overflow-hidden rounded-md border border-line">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-line bg-ink-800/60 text-xs uppercase tracking-wide text-text-faint">
                  <tr>
                    <th className="px-3 py-2 font-medium">Source</th>
                    <th className="px-3 py-2 font-medium">Relationship</th>
                    <th className="px-3 py-2 font-medium">Target</th>
                    <th className="px-3 py-2 font-medium">Location</th>
                    <th className="px-3 py-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/60">
                  {relationships.map((rel) => {
                    const { source, target } = describe(rel);
                    const expanded = expandedId === rel.id;
                    return (
                      <Fragment key={rel.id}>
                        <tr
                          className="cursor-pointer transition-colors hover:bg-ink-600/40"
                          onClick={() =>
                            setExpandedId(expanded ? null : rel.id)
                          }
                        >
                          <td className="max-w-56 px-3 py-2 font-mono text-[13px] text-text-primary">
                            <span className="mr-1.5 inline-flex align-middle text-text-faint">
                              {expanded ? (
                                <ChevronDown className="h-3.5 w-3.5" />
                              ) : (
                                <ChevronRight className="h-3.5 w-3.5" />
                              )}
                            </span>
                            {source}
                          </td>
                          <td className="px-3 py-2">
                            <Badge tone={typeTone(rel.type)}>{rel.type}</Badge>
                          </td>
                          <td className="max-w-56 truncate px-3 py-2 font-mono text-[13px] text-text-secondary">
                            {target}
                          </td>
                          <td className="max-w-40 truncate px-3 py-2 font-mono text-xs text-text-muted">
                            {rel.source_file ? `${rel.source_file}:${rel.source_line}` : "—"}
                          </td>
                          <td className="px-3 py-2">
                            <Badge tone={statusTone(rel.resolution_status)}>
                              {rel.resolution_status}
                            </Badge>
                          </td>
                        </tr>
                        {expanded && (
                          <tr className="border-b border-line/60 bg-ink-800/80">
                            <td colSpan={5} className="px-4 py-3">
                              <p className="mb-2 text-xs leading-relaxed text-text-primary">
                                {rel.evidence ?? "No evidence description recorded."}
                              </p>
                              <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-text-muted">
                                <span>
                                  source file: {rel.source_file ?? "—"}
                                </span>
                                <span>target file: {rel.target_file ?? "—"}</span>
                                <span>target line: {rel.target_line ?? "—"}</span>
                                <span>
                                  source type: {rel.source_type ?? "—"}
                                </span>
                                <span>
                                  target type: {rel.target_type ?? "—"}
                                </span>
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {!loading && total > relationships.length && (
            <p className="text-right text-xs text-text-faint">
              Showing first {relationships.length.toLocaleString()} of{" "}
              {total.toLocaleString()}. Refine filters to narrow the list.
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}
