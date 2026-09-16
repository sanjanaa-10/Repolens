import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { investigateSymbol } from "../../api/client";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/Badge";
import type {
  InvestigationGroup,
  InvestigationResponse,
  RelationshipInfo,
  SymbolInfo,
} from "../../types";

interface InvestigationPanelProps {
  repositoryId: number;
  symbol: SymbolInfo | null;
  onNavigate: (filePath: string, line: number, name: string) => void;
}

type BadgeTone =
  | "neutral"
  | "accent"
  | "success"
  | "warning"
  | "danger"
  | "muted";

const TYPE_TONES: Record<string, BadgeTone> = {
  CALLS: "success",
  IMPORTS: "accent",
  EXPORTS: "accent",
  REFERENCES: "warning",
  EXTENDS: "danger",
  IMPLEMENTS: "danger",
  TESTS: "muted",
  DEFINES: "neutral",
};

const GROUP_COLORS: Record<string, string> = {
  callers: "text-warning",
  callees: "text-success",
  references: "text-accent-hover",
  dependencies: "text-danger",
  tests: "text-text-secondary",
  exports: "text-text-secondary",
  imports: "text-accent-hover",
};

function typeTone(type: string): BadgeTone {
  return TYPE_TONES[type] ?? "neutral";
}

function relate(edge: RelationshipInfo) {
  return {
    name: edge.target_symbol_name ?? edge.target_file ?? "unresolved",
    file: edge.target_file ?? edge.source_file,
    line: edge.target_line ?? edge.source_line,
  };
}

export function InvestigationPanel({
  repositoryId,
  symbol,
  onNavigate,
}: InvestigationPanelProps) {
  const [data, setData] = useState<InvestigationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let active = true;
    setData(null);
    setError(null);
    setExpanded({});
    if (!symbol) return;
    setLoading(true);
    investigateSymbol(repositoryId, symbol.id)
      .then((inv) => {
        if (active) setData(inv);
      })
      .catch((err: unknown) => {
        if (active) {
          setError(
            (err as { response?: { data?: { detail?: string } } })?.response
              ?.data?.detail ?? "Failed to investigate symbol."
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [repositoryId, symbol]);

  const toggle = useCallback((key: string) => {
    setExpanded((prev) => ({ ...prev, [key]: prev[key] ? false : true }));
  }, []);

  if (!symbol) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-sm text-text-muted">
          Select a symbol to see its callers, callees, references, and tests.
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-line bg-ink-900 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-medium text-text-primary">
            {symbol.name}
          </span>
          <Badge tone="neutral">{symbol.kind}</Badge>
          {symbol.exported && <Badge tone="success">export</Badge>}
        </div>
        {symbol.qualified_name && (
          <p className="mt-1 truncate font-mono text-xs text-text-faint">
            {symbol.qualified_name}
          </p>
        )}
        <button
          type="button"
          onClick={() => onNavigate(symbol.file_path, symbol.line_start, symbol.name)}
          className="mt-1.5 truncate font-mono text-xs text-accent-hover hover:underline"
        >
          {symbol.file_path}:{symbol.line_start}
        </button>
        {symbol.signature && symbol.signature !== symbol.name && (
          <p className="mt-1 truncate font-mono text-[11px] text-text-muted">
            {symbol.signature}
          </p>
        )}
      </div>

      <div className="flex-1 overflow-auto">
        {loading && (
          <div className="flex items-center justify-center gap-2 p-6 text-sm text-text-muted">
            <Loader2 className="h-4 w-4 animate-spin" /> Investigating…
          </div>
        )}

        {error && <p className="px-4 py-4 text-sm text-danger">{error}</p>}

        {data && (
          <div className="flex flex-col gap-2 p-3">
            {data.groups.length === 0 && data.unresolved.length === 0 && data.external.length === 0 && (
              <p className="px-1 py-2 text-sm text-text-muted">
                No graph relationships found for this symbol.
              </p>
            )}

            {data.groups.map((group: InvestigationGroup) => {
              const key = group.category;
              const isOpen = expanded[key] ?? group.count <= 5;
              return (
                <div
                  key={key}
                  className="overflow-hidden rounded-md border border-line"
                >
                  <button
                    type="button"
                    onClick={() => toggle(key)}
                    className="flex w-full items-center justify-between bg-ink-800/60 px-3 py-2 text-left"
                  >
                    <span className="flex items-center gap-2">
                      <span
                        className={cn(
                          "text-xs font-semibold uppercase tracking-wide",
                          GROUP_COLORS[key] ?? "text-text-secondary"
                        )}
                      >
                        {group.label}
                      </span>
                      <Badge tone="neutral">{group.count}</Badge>
                    </span>
                    {isOpen ? (
                      <ChevronDown className="h-3.5 w-3.5 text-text-faint" />
                    ) : (
                      <ChevronRight className="h-3.5 w-3.5 text-text-faint" />
                    )}
                  </button>
                  {isOpen && (
                    <ul className="divide-y divide-line/40">
                      {group.edges.map((edge) => {
                        const { name, file, line } = relate(edge);
                        return (
                          <li
                            key={edge.id}
                            className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-ink-700/50"
                          >
                            <Badge tone={typeTone(edge.type)}>{edge.type}</Badge>
                            <button
                              type="button"
                              className="truncate font-mono text-[13px] text-text-primary hover:text-accent-hover"
                              onClick={() => {
                                if (edge.target_symbol_id) {
                                  const targetFile =
                                    edge.target_file ?? file ?? edge.source_file;
                                  onNavigate(
                                    targetFile ?? "",
                                    line ?? 0,
                                    name ?? edge.target_symbol_id.toString()
                                  );
                                }
                              }}
                            >
                              {name}
                            </button>
                            {file && line != null && (
                              <span className="ml-auto shrink-0 font-mono text-[11px] text-text-faint">
                                {file.split("/").pop()}:{line}
                              </span>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              );
            })}

            {(data.unresolved.length > 0 || data.external.length > 0) && (
              <div className="mt-1 flex flex-col gap-2 px-1">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">
                  Unresolved / External
                </p>
                {[...data.unresolved, ...data.external].map((edge) => (
                  <div
                    key={edge.id}
                    className="flex items-center gap-2 text-sm"
                  >
                    <Badge tone="muted">{edge.resolution_status}</Badge>
                    <span className="truncate font-mono text-xs text-text-muted">
                      {edge.source_symbol_name ?? edge.source_file} →{" "}
                      {edge.target_symbol_name ?? edge.target_file ?? "?"}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {data.related_files.length > 0 && (
              <div className="mt-1 px-1">
                <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-text-faint">
                  Related files
                </p>
                <ul className="space-y-1">
                  {data.related_files.map((file) => (
                    <li key={file.file_id}>
                      <button
                        type="button"
                        onClick={() => onNavigate(file.path, 1, file.path)}
                        className="w-full truncate text-left font-mono text-xs text-text-secondary hover:text-accent-hover"
                      >
                        {file.path}
                        <span className="ml-2 text-text-faint">
                          {file.symbol_count} symbols
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
