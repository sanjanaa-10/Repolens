import { useCallback, useEffect, useRef, useState } from "react";
import { FileCode2, FileSearch, Loader2, Search } from "lucide-react";
import { getSymbolDetail, search } from "../../api/client";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/Badge";
import { Input } from "../ui/Input";
import { InvestigationPanel } from "./InvestigationPanel";
import { SourceView } from "./SourceView";
import type {
  FileSearchHit,
  SearchHit,
  SearchType,
  SymbolInfo,
  SymbolSearchHit,
  TextSearchHit,
} from "../../types";

interface SearchWorkspaceProps {
  repositoryId: number;
  initialSymbolId?: number | null;
  initialFile?: string;
}

type BadgeTone =
  | "neutral"
  | "accent"
  | "success"
  | "warning"
  | "danger"
  | "muted";

const KIND_TONES: Record<string, BadgeTone> = {
  FUNCTION: "accent",
  ASYNC_FUNCTION: "accent",
  CLASS: "success",
  METHOD: "success",
  INTERFACE: "warning",
  TYPE_ALIAS: "warning",
  ENUM: "warning",
  VARIABLE: "neutral",
  IMPORT: "muted",
};

function kindTone(kind: string): BadgeTone {
  return KIND_TONES[kind] ?? "neutral";
}

function isSymbolHit(hit: SearchHit): hit is SymbolSearchHit {
  return "symbol_id" in hit;
}
function isFileHit(hit: SearchHit): hit is FileSearchHit {
  return "file_id" in hit && !("line_number" in hit);
}
function isTextHit(hit: SearchHit): hit is TextSearchHit {
  return "line_number" in hit;
}

export function SearchWorkspace({
  repositoryId,
  initialSymbolId,
  initialFile,
}: SearchWorkspaceProps) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState<SearchType>("symbol");
  const [results, setResults] = useState<SearchHit[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Source view state
  const [viewFile, setViewFile] = useState<string | null>(null);
  const [highlightLine, setHighlightLine] = useState<number | null>(null);
  const [viewName, setViewName] = useState<string | null>(null);

  // Investigation state
  const [selectedSymbol, setSelectedSymbol] = useState<SymbolInfo | null>(null);

  // Deep-link: jump straight to a changed symbol's investigation.
  useEffect(() => {
    if (!initialSymbolId) return;
    let active = true;
    getSymbolDetail(repositoryId, initialSymbolId)
      .then((symbol) => {
        if (!active) return;
        setSelectedSymbol(symbol);
        setViewFile(symbol.file_path);
        setHighlightLine(symbol.line_start);
        setViewName(symbol.name);
      })
      .catch(() => {
        /* symbol may have been deleted in HEAD — ignore */
      });
    return () => {
      active = false;
    };
  }, [repositoryId, initialSymbolId]);

  // Deep-link: open a file directly in the source view.
  useEffect(() => {
    if (!initialFile) return;
    setViewFile(initialFile);
    setHighlightLine(null);
    setViewName(null);
  }, [initialFile]);

  const searchTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      setTotal(0);
      return;
    }
    setLoading(true);
    setError(null);
    if (searchTimer.current) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(async () => {
      try {
        const data = await search(
          repositoryId,
          query.trim(),
          type,
          50,
          0,
          false
        );
        setResults(data.results ?? []);
        setTotal(data.total ?? 0);
      } catch (err: unknown) {
        setError(
          (err as { response?: { data?: { detail?: string } } })?.response
            ?.data?.detail ?? "Search failed."
        );
        setResults([]);
        setTotal(0);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => {
      if (searchTimer.current) window.clearTimeout(searchTimer.current);
    };
  }, [repositoryId, query, type]);

  const navigateTo = useCallback(
    (filePath: string, line: number, name: string) => {
      setViewFile(filePath);
      setHighlightLine(line);
      setViewName(name);
    },
    []
  );

  const openSymbol = useCallback((hit: SymbolSearchHit) => {
    setSelectedSymbol({
      id: hit.symbol_id,
      name: hit.name,
      qualified_name: hit.qualified_name,
      kind: hit.kind,
      language: hit.language,
      file_path: hit.file_path,
      line_start: hit.line_start,
      line_end: hit.line_end,
      start_column: 0,
      end_column: 0,
      exported: hit.exported,
      signature: hit.signature,
      docstring: null,
      parent_symbol_id: null,
    });
    navigateTo(hit.file_path, hit.line_start, hit.name);
  }, [navigateTo]);

  const renderResult = (hit: SearchHit) => {
    if (isSymbolHit(hit)) {
      return (
        <button
          type="button"
          onClick={() => openSymbol(hit)}
          className="flex w-full items-start gap-3 px-4 py-2.5 text-left transition-colors hover:bg-ink-700/50"
        >
          <span
            className={cn(
              "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded border border-line bg-ink-800"
            )}
          >
            <Search className="h-3 w-3 text-text-faint" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-2">
              <span className="font-mono text-[13px] font-medium text-text-primary">
                {hit.name}
              </span>
              <Badge tone={kindTone(hit.kind)}>{hit.kind}</Badge>
              {hit.exported && <Badge tone="success">export</Badge>}
            </span>
            <span className="mt-0.5 block truncate font-mono text-xs text-text-muted">
              {hit.file_path}:{hit.line_start}
            </span>
            {hit.qualified_name && (
              <span className="mt-0.5 block truncate font-mono text-[11px] text-text-faint">
                {hit.qualified_name}
              </span>
            )}
          </span>
        </button>
      );
    }
    if (isFileHit(hit)) {
      return (
        <button
          type="button"
          onClick={() => navigateTo(hit.path, 1, hit.path)}
          className="flex w-full items-start gap-3 px-4 py-2.5 text-left transition-colors hover:bg-ink-700/50"
        >
          <FileCode2 className="mt-0.5 h-4 w-4 shrink-0 text-accent-hover" />
          <span className="min-w-0 flex-1">
            <span className="block truncate font-mono text-[13px] text-text-primary">
              {hit.path}
            </span>
            <span className="mt-0.5 block text-xs text-text-muted">
              {hit.language ?? "unknown"} · {hit.symbol_count} symbols
            </span>
          </span>
        </button>
      );
    }
    if (isTextHit(hit)) {
      return (
        <button
          type="button"
          onClick={() => navigateTo(hit.path, hit.line_number, query)}
          className="flex w-full items-start gap-3 px-4 py-2.5 text-left transition-colors hover:bg-ink-700/50"
        >
          <FileSearch className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
          <span className="min-w-0 flex-1">
            <span className="block truncate font-mono text-xs text-text-secondary">
              {hit.path}:{hit.line_number}
            </span>
            <span className="mt-0.5 block truncate font-mono text-[12px] text-text-muted">
              {hit.snippet}
            </span>
          </span>
        </button>
      );
    }
    return null;
  };

  const typeTabs: { key: SearchType; label: string }[] = [
    { key: "symbol", label: "Symbols" },
    { key: "file", label: "Files" },
    { key: "text", label: "Text" },
  ];

  return (
    <div className="flex h-full flex-1">
      {/* Left: repository search + results */}
      <div className="flex w-80 shrink-0 flex-col border-r border-line bg-ink-900">
        <div className="border-b border-line p-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-faint" />
            <Input
              className="pl-9"
              placeholder="Search symbols, files, or text…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoFocus
            />
          </div>
          <div className="mt-2 flex items-center gap-1">
            {typeTabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setType(tab.key)}
                className={cn(
                  "flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors border",
                  type === tab.key
                    ? "border-accent bg-accent/15 text-accent-hover"
                    : "border-line bg-ink-800 text-text-secondary hover:border-ink-300"
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-auto">
          {error && <p className="px-4 py-3 text-sm text-danger">{error}</p>}

          {loading && (
            <div className="flex items-center justify-center gap-2 p-6 text-sm text-text-muted">
              <Loader2 className="h-4 w-4 animate-spin" /> Searching…
            </div>
          )}

          {!loading && query.trim() && total > 0 && (
            <p className="border-b border-line/40 px-4 py-1.5 text-xs text-text-faint">
              {total.toLocaleString()} result{total === 1 ? "" : "s"}
            </p>
          )}

          {!loading && query.trim() && total === 0 && !error && (
            <p className="px-4 py-6 text-sm text-text-muted">
              No matches found for “{query}”.
            </p>
          )}

          {!loading && !query.trim() && (
            <p className="px-4 py-6 text-sm text-text-muted">
              Type a query to search the repository index. Search is offline and
              deterministic — no LLM involved.
            </p>
          )}

          {!loading && results.map((hit, i) => (
            <div
              key={i}
              className="border-b border-line/40 hover:bg-ink-700/30"
            >
              {renderResult(hit)}
            </div>
          ))}
        </div>

        <div className="border-t border-line p-2">
          <Badge tone="muted">Ctrl+K to search</Badge>
        </div>
      </div>

      {/* Center: source view */}
      <div className="flex-1 overflow-hidden border-r border-line">
        <SourceView
          repositoryId={repositoryId}
          filePath={viewFile}
          highlightLine={highlightLine}
          symbolName={viewName}
        />
      </div>

      {/* Right: investigation context */}
      <div className="flex w-80 shrink-0 flex-col overflow-hidden bg-ink-900">
        <InvestigationPanel
          repositoryId={repositoryId}
          symbol={selectedSymbol}
          onNavigate={navigateTo}
        />
      </div>
    </div>
  );
}
