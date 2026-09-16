import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { getSymbols } from "../../api/client";
import { Badge } from "../ui/Badge";
import { Input } from "../ui/Input";
import { Panel } from "../ui/Panel";
import type { SymbolInfo } from "../../types";

interface SymbolBrowserProps {
  repositoryId: number;
  enabled: boolean;
}

const KINDS = [
  "FUNCTION",
  "ASYNC_FUNCTION",
  "CLASS",
  "METHOD",
  "ARROW_FUNCTION",
  "INTERFACE",
  "TYPE_ALIAS",
  "ENUM",
  "VARIABLE",
  "IMPORT",
] as const;

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
  ARROW_FUNCTION: "accent",
  INTERFACE: "warning",
  TYPE_ALIAS: "warning",
  ENUM: "warning",
  VARIABLE: "neutral",
  IMPORT: "muted",
};

function kindTone(kind: string): BadgeTone {
  return KIND_TONES[kind] ?? "neutral";
}

function formatLine(symbol: SymbolInfo): string {
  return symbol.line_start === symbol.line_end
    ? `L${symbol.line_start}`
    : `L${symbol.line_start}–${symbol.line_end}`;
}

export function SymbolBrowser({ repositoryId, enabled }: SymbolBrowserProps) {
  const [kind, setKind] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    setLoading(true);
    setError(null);
    const timer = window.setTimeout(async () => {
      try {
        const data = await getSymbols(repositoryId, {
          name: name || undefined,
          kind: kind ?? undefined,
          limit: 300,
        });
        if (active) {
          setSymbols(data.items);
          setTotal(data.total);
        }
      } catch (err: unknown) {
        if (active) {
          setError(
            (err as { response?: { data?: { detail?: string } } })?.response
              ?.data?.detail ?? "Failed to load symbols."
          );
          setSymbols([]);
          setTotal(0);
        }
      } finally {
        if (active) setLoading(false);
      }
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [repositoryId, enabled, kind, name]);

  const range = useMemo(() => {
    if (total === 0 || symbols.length >= total) return null;
    return `${symbols.length.toLocaleString()} of ${total.toLocaleString()}`;
  }, [symbols.length, total]);

  return (
    <Panel
      title="Symbols"
      subtitle="Functions, classes, methods, interfaces, types, and imports"
      actions={
        enabled ? (
          <Badge tone={loading ? "muted" : "accent"}>
            {loading ? "loading…" : `${total.toLocaleString()} matched`}
          </Badge>
        ) : undefined
      }
    >
      {!enabled ? (
        <p className="text-sm text-text-muted">
          Run the analysis above to browse the extracted symbol index.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-56 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-faint" />
              <Input
                className="pl-9"
                placeholder="Filter by symbol name…"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div className="flex flex-wrap items-center gap-1">
              <button
                className={`rounded-full px-2.5 py-1 text-xs font-medium transition-colors border ${
                  kind === null
                    ? "border-accent bg-accent/15 text-accent-hover"
                    : "border-line bg-ink-800 text-text-secondary hover:border-ink-300"
                }`}
                onClick={() => setKind(null)}
              >
                All
              </button>
              {KINDS.map((item) => (
                <button
                  key={item}
                  className={`rounded-full px-2.5 py-1 text-xs font-medium transition-colors border ${
                    kind === item
                      ? "border-accent bg-accent/15 text-accent-hover"
                      : "border-line bg-ink-800 text-text-secondary hover:border-ink-300"
                  }`}
                  onClick={() => setKind(kind === item ? null : item)}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>

          {error && <p className="text-sm text-danger">{error}</p>}

          <div className="overflow-hidden rounded-md border border-line">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-line bg-ink-800/60 text-xs uppercase tracking-wide text-text-faint">
                <tr>
                  <th className="px-3 py-2 font-medium">Symbol</th>
                  <th className="px-3 py-2 font-medium">Kind</th>
                  <th className="px-3 py-2 font-medium">File</th>
                  <th className="px-3 py-2 text-right font-medium">Lines</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/60">
                {symbols.length === 0 && !loading && (
                  <tr>
                    <td colSpan={4} className="px-3 py-6 text-center text-text-muted">
                      No symbols match the current filters.
                    </td>
                  </tr>
                )}
                {symbols.map((symbol) => (
                  <tr
                    key={symbol.id}
                    className="transition-colors hover:bg-ink-600/40"
                  >
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[13px] text-text-primary">
                          {symbol.name}
                        </span>
                        {symbol.exported && <Badge tone="success">export</Badge>}
                      </div>
                      {symbol.signature && symbol.signature !== symbol.name && (
                        <div className="font-mono text-xs text-text-faint">
                          {symbol.signature}
                        </div>
                      )}
                      {symbol.qualified_name && (
                        <div className="mt-0.5 truncate font-mono text-[11px] text-text-faint">
                          {symbol.qualified_name}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={kindTone(symbol.kind) as never}>
                        {symbol.kind}
                      </Badge>
                    </td>
                    <td className="max-w-60 truncate px-3 py-2 font-mono text-xs text-text-secondary">
                      {symbol.file_path}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-xs text-text-muted">
                      {formatLine(symbol)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {range && (
            <p className="text-right text-xs text-text-faint">
              Showing first {range}. Refine filters to narrow the list.
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}