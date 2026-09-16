import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  Braces,
  Download,
  Loader2,
  LogOut,
  Package,
  PlayCircle,
  Timer,
} from "lucide-react";
import { getAnalysis, parseRepository } from "../../api/client";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Panel } from "../ui/Panel";
import type { AnalysisSummary } from "../../types";

interface AnalysisPanelProps {
  repositoryId: number;
  repoAnalyzed: boolean;
  onAnalyzed: () => void;
}

function StatTile({
  label,
  value,
  icon,
  tone = "text-text-primary",
}: {
  label: string;
  value: string;
  icon: React.ReactNode;
  tone?: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-ink-800 p-4">
      <p className="flex items-center gap-1.5 text-xs text-text-muted">
        {icon}
        {label}
      </p>
      <p className={`mt-1.5 font-mono text-xl font-semibold ${tone}`}>
        {value}
      </p>
    </div>
  );
}

export function AnalysisPanel({
  repositoryId,
  repoAnalyzed,
  onAnalyzed,
}: AnalysisPanelProps) {
  const [summary, setSummary] = useState<AnalysisSummary | null>(null);
  const [loading, setLoading] = useState(repoAnalyzed);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getAnalysis(repositoryId);
      setSummary(data);
      if (data.analyzed) onAnalyzed();
    } catch (err: unknown) {
      setError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Failed to load the analysis summary."
      );
    } finally {
      setLoading(false);
    }
  }, [repositoryId, onAnalyzed]);

  const run = useCallback(async () => {
    setRunning(true);
    setError(null);
    try {
      const data = await parseRepository(repositoryId);
      setSummary(data);
      onAnalyzed();
    } catch (err: unknown) {
      setError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "The analysis run failed. Please try again."
      );
    } finally {
      setRunning(false);
    }
  }, [repositoryId, onAnalyzed]);

  useEffect(() => {
    if (repoAnalyzed) {
      void refresh();
    }
  }, [repoAnalyzed, refresh]);

  const s = summary;

  return (
    <Panel
      title="Deterministic structure analysis"
      subtitle="tree-sitter pass over every stored source file — none of the code is executed"
      actions={
        repoAnalyzed || s?.analyzed ? (
          <Badge tone="success">Parsed</Badge>
        ) : (
          <Badge tone="muted">Not analyzed</Badge>
        )
      }
    >
      {!repoAnalyzed && !s?.analyzed && (
        <div className="flex items-center justify-between gap-4">
          <p className="max-w-lg text-sm text-text-muted">
            This repository has been ingested but not yet analyzed. Parsing
            extracts symbols, imports, exports, and their exact source
            locations.
          </p>
          <Button onClick={run} isLoading={running} disabled={loading}>
            {running ? null : <PlayCircle className="h-4 w-4" />}
            {running ? "Parsing…" : "Run analysis"}
          </Button>
        </div>
      )}

      {error && (
        <div className="mt-4 flex items-start gap-3 rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {loading && !s && (
        <div className="mt-4 flex items-center justify-center py-6 text-sm text-text-muted">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading analysis summary…
        </div>
      )}

      {s?.analyzed && (
        <div className="flex flex-col gap-4">
          <div className="mt-1 grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
            <StatTile
              label="Files processed"
              value={`${s.files_processed} / ${s.file_count}`}
              icon={<Package className="h-3.5 w-3.5 text-accent-hover" />}
            />
            <StatTile
              label="Symbols"
              value={s.symbol_count.toLocaleString()}
              icon={<Braces className="h-3.5 w-3.5 text-accent-hover" />}
            />
            <StatTile
              label="Imports"
              value={s.import_count.toLocaleString()}
              icon={<Download className="h-3.5 w-3.5 text-accent-hover" />}
            />
            <StatTile
              label="Exports"
              value={s.export_count.toLocaleString()}
              icon={<LogOut className="h-3.5 w-3.5 text-accent-hover" />}
            />
          </div>

          <div className="rounded-md border border-line bg-ink-800 px-4 py-3">
            <p className="mb-2 text-xs text-text-muted">Per-file status</p>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="success">
                parsed {s.statuses.parsed}
              </Badge>
              <Badge tone="warning">
                syntax errors {s.statuses.syntax_error}
              </Badge>
              <Badge tone="muted">
                unsupported {s.statuses.unsupported}
              </Badge>
              {s.statuses.failed > 0 && (
                <Badge tone="danger">failed {s.statuses.failed}</Badge>
              )}
              <span className="ml-auto inline-flex items-center gap-1 font-mono text-xs text-text-faint">
                <Timer className="h-3.5 w-3.5" />
                {s.duration_ms.toLocaleString()} ms
              </span>
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}