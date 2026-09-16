import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import {
  AlertCircle,
  Check,
  Copy,
  FileCode2,
  Loader2,
  ShieldCheck,
} from "lucide-react";
import { getFileContent } from "../../api/client";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/Badge";

export interface HighlightRange {
  start: number;
  end: number;
}

interface SourceViewProps {
  repositoryId: number;
  filePath: string | null;
  highlightLine?: number | null;
  highlightRange?: HighlightRange | null;
  symbolName?: string | null;
  onSymbolClick?: (filePath: string, line: number, name: string) => void;
}

const LANG: Record<string, string> = {
  python: "python",
  javascript: "javascript",
  typescript: "typescript",
  jsx: "jsx",
  tsx: "tsx",
  json: "json",
  css: "css",
  html: "markup",
};

export function SourceView({
  repositoryId,
  filePath,
  highlightLine,
  highlightRange,
  symbolName,
  onSymbolClick,
}: SourceViewProps) {
  const [content, setContent] = useState<string | null>(null);
  const [language, setLanguage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const lineRefs = useRef<Record<number, HTMLTableRowElement | null>>({});

  useEffect(() => {
    let active = true;
    setContent(null);
    setError(null);
    if (!filePath) return;
    setLoading(true);
    getFileContent(repositoryId, filePath)
      .then((data) => {
        if (!active) return;
        setContent(data.content);
        setLanguage(data.language ?? null);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError(
          (err as { response?: { data?: { detail?: string } } })?.response
            ?.data?.detail ?? "Failed to load file."
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [repositoryId, filePath]);

  // Scroll highlight line into view
  useEffect(() => {
    if (highlightLine && lineRefs.current[highlightLine]) {
      lineRefs.current[highlightLine]?.scrollIntoView({
        block: "center",
        behavior: "smooth",
      });
    }
  }, [highlightLine, content]);

  const handleCopy = useCallback(async () => {
    if (!filePath) return;
    try {
      await navigator.clipboard.writeText(filePath);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard unavailable — ignore.
    }
  }, [filePath]);

  const lines = useMemo(
    () => (content ? content.split("\n") : []),
    [content]
  );

  if (!filePath) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <FileCode2 className="h-8 w-8 text-text-faint" />
        <p className="text-sm text-text-muted">
          Select a search result or related symbol to view its source.
        </p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-text-muted">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading {filePath}…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center gap-3 p-6 text-center">
        <AlertCircle className="h-8 w-8 text-danger" />
        <div>
          <p className="text-sm text-danger">{error}</p>
          <p className="mt-1 font-mono text-xs text-text-muted">{filePath}</p>
        </div>
      </div>
    );
  }

  const codeLang = language
    ? (LANG[language.toLowerCase()] ?? "text")
    : "text";

  const handleLineClick = (line: string, lineNo: number) => {
    if (!onSymbolClick) return;
    const trimmed = line.trim();
    const nameMatch = trimmed.match(
      /^(?:async\s+)?(?:export\s+(?:default\s+)?)?(?:function|def|class|const|let|var)\s+([A-Za-z_$][\w$]*)/
    );
    if (nameMatch && nameMatch[1]) {
      onSymbolClick(filePath, lineNo, nameMatch[1]);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-line bg-ink-900 px-4 py-2">
        <div className="flex min-w-0 items-center gap-2 font-mono text-xs text-text-secondary">
          <FileCode2 className="h-3.5 w-3.5 shrink-0 text-accent-hover" />
          <span className="truncate">{filePath}</span>
          <button
            type="button"
            onClick={() => void handleCopy()}
            aria-label="Copy file path"
            className="shrink-0 text-text-faint transition-colors hover:text-text-primary"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-success" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {language && <Badge tone="muted">{language}</Badge>}
          <Badge tone="muted">
            <ShieldCheck className="mr-1 h-3 w-3" />
            Read-only
          </Badge>
        </div>
      </div>
      <div className="flex-1 overflow-auto bg-ink-950">
        <table className="w-full border-collapse font-mono text-[13px] leading-relaxed">
          <tbody>
            {lines.map((line, idx) => {
              const lineNo = idx + 1;
              const highlighted =
                highlightLine != null && lineNo === highlightLine;
              const ranged =
                highlightRange != null &&
                lineNo >= highlightRange.start &&
                lineNo <= highlightRange.end;
              return (
                <tr
                  key={lineNo}
                  ref={(el) => {
                    lineRefs.current[lineNo] = el;
                  }}
                  onClick={() => handleLineClick(line, lineNo)}
                  className={cn(
                    "group",
                    (highlighted || ranged) && "bg-accent/15",
                    onSymbolClick && "cursor-default"
                  )}
                >
                  <td
                    className={cn(
                      "w-12 select-none border-r border-line/40 px-2 text-right align-top text-text-faint",
                      (highlighted || ranged) &&
                        "bg-accent/15 text-accent-hover"
                    )}
                  >
                    {lineNo}
                  </td>
                  <td className="py-0 pl-3 pr-2">
                    <SyntaxHighlighter
                      language={codeLang}
                      style={oneDark}
                      customStyle={{
                        background: "transparent",
                        margin: 0,
                        padding: 0,
                      }}
                      codeTagProps={{
                        style: { fontSize: "13px", lineHeight: "1.6" },
                      }}
                    >
                      {line}
                    </SyntaxHighlighter>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {symbolName && (
        <div className="border-t border-line bg-ink-800 px-4 py-1.5 text-xs text-text-muted">
          Selected symbol:{" "}
          <span className="font-mono text-accent-hover">{symbolName}</span>
        </div>
      )}
    </div>
  );
}
