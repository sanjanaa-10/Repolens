import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ClipboardCheck, GitCompare, Network, Loader2 } from "lucide-react";
import { Link } from "react-router-dom";
import { getRecentActivity } from "../../api/client";
import { Badge } from "../ui/Badge";
import type { RecentActivityInfo } from "../../types";

interface RecentActivityProps {
  repositoryId: number;
}

function shortSha(sha: string | null): string {
  return sha ? sha.slice(0, 7) : "";
}

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function RecentActivity({ repositoryId }: RecentActivityProps) {
  const [data, setData] = useState<RecentActivityInfo | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    getRecentActivity(repositoryId)
      .then((info) => {
        if (active) setData(info);
      })
      .catch(() => {
        if (active) setData(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [repositoryId]);

  const hasAny =
    data &&
    (data.diffs.length > 0 ||
      data.impact_analyses.length > 0 ||
      data.reviews.length > 0);

  return (
    <div>
      {loading && (
        <p className="flex items-center gap-2 text-sm text-text-muted">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading recent activity…
        </p>
      )}

      {!loading && !hasAny && (
        <p className="text-sm text-text-muted">
          No change analyses yet. Run an investigation or simulate a change to
          get started.
        </p>
      )}

      {hasAny && data && (
        <div className="flex flex-col gap-3">
          {data.diffs.length > 0 && (
            <Group title="Diffs" icon={<GitCompare className="h-3.5 w-3.5" />}>
              {data.diffs.map((d) => (
                <Link
                  key={d.id}
                  to={`/repo/${repositoryId}/changes`}
                  className="flex items-center gap-2 rounded-md border border-line bg-ink-800/70 px-3 py-1.5 transition-colors hover:border-accent/40"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-text-primary">
                    {shortSha(d.base_revision)} → {shortSha(d.head_revision)}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-text-faint">
                    {d.files_changed} files · {d.symbols_changed} symbols
                  </span>
                  <span className="shrink-0 text-[10px] text-text-muted">
                    {formatWhen(d.computed_at)}
                  </span>
                </Link>
              ))}
            </Group>
          )}

          {data.impact_analyses.length > 0 && (
            <Group title="Impact simulations" icon={<Network className="h-3.5 w-3.5" />}>
              {data.impact_analyses.map((a) => (
                <Link
                  key={a.analysis_id}
                  to={`/repo/${repositoryId}/impact?analysis=${a.analysis_id}`}
                  className="flex items-center gap-2 rounded-md border border-line bg-ink-800/70 px-3 py-1.5 transition-colors hover:border-accent/40"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-text-primary">
                    {shortSha(a.base_revision)} → {shortSha(a.head_revision)}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-text-faint">
                    {a.summary.potential} potential · {a.summary.unresolved}{" "}
                    unresolved
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-text-faint">
                    depth {a.max_depth}
                  </span>
                </Link>
              ))}
            </Group>
          )}

          {data.reviews.length > 0 && (
            <Group title="Change reviews" icon={<ClipboardCheck className="h-3.5 w-3.5" />}>
              {data.reviews.map((r) => (
                <Link
                  key={r.review_id}
                  to={`/repo/${repositoryId}/review?review=${r.review_id}`}
                  className="flex items-center gap-2 rounded-md border border-line bg-ink-800/70 px-3 py-1.5 transition-colors hover:border-accent/40"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-text-primary">
                    {shortSha(r.base_revision)} → {shortSha(r.head_revision)}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-text-faint">
                    {r.review_summary.total_items} items ·{" "}
                    {r.review_summary.completed} done
                  </span>
                  <Badge tone={r.status === "DONE" ? "success" : "neutral"}>
                    {r.status}
                  </Badge>
                  <span className="shrink-0 text-[10px] text-text-muted">
                    {formatWhen(r.updated_at)}
                  </span>
                </Link>
              ))}
            </Group>
          )}
        </div>
      )}
    </div>
  );
}

function Group({
  title,
  icon,
  children,
}: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <div>
      <p className="mb-1 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-text-faint">
        {icon}
        {title}
      </p>
      <div className="flex flex-col gap-1.5">{children}</div>
    </div>
  );
}