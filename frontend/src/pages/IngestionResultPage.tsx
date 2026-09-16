import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CircleDot,
  ClipboardCheck,
  ExternalLink,
  FileCode2,
  GitBranch,
  GitCompare,
  GitCommitHorizontal,
  Network,
  Search,
} from "lucide-react";
import { getRepository } from "../api/client";
import { RecentActivity } from "../components/analysis/RecentActivity";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { formatNumber } from "../lib/utils";
import type { AnalysisStatus, RepositoryInfo } from "../types";

const STATUS_TONE: Record<AnalysisStatus, "neutral" | "success" | "danger" | "accent" | "warning"> =
  {
    pending: "neutral",
    cloning: "accent",
    indexing: "accent",
    ready: "success",
    failed: "danger",
  };

function shortSha(sha: string | null): string {
  return sha ? sha.slice(0, 7) : "—";
}

interface ActionCardProps {
  to: string;
  icon: React.ReactNode;
  title: string;
  description: string;
}

function ActionCard({ to, icon, title, description }: ActionCardProps) {
  return (
    <Link
      to={to}
      className="group flex flex-col gap-1 rounded-lg border border-line bg-surface px-4 py-3.5 transition-colors hover:border-accent/40 hover:bg-surface-hover focus-visible:ring-2 focus-visible:ring-accent"
    >
      <span className="flex items-center gap-2.5">
        <span className="flex h-8 w-8 items-center justify-center rounded-md bg-ink-600 text-accent-hover transition-colors group-hover:bg-accent/15">
          {icon}
        </span>
        <span className="text-sm font-semibold tracking-tight text-text-primary">
          {title}
        </span>
      </span>
      <p className="text-xs leading-relaxed text-text-muted">{description}</p>
    </Link>
  );
}

export function IngestionResultPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [repo, setRepo] = useState<RepositoryInfo | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const data = await getRepository(Number(id));
        if (active) setRepo(data);
      } catch (err: unknown) {
        if (active) {
          const status = (err as { response?: { status?: number } })?.response?.status;
          setNotFound(status === 404);
          setError(
            (err as { response?: { data?: { detail?: string } } })?.response?.data
              ?.detail ?? null
          );
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [id]);

  if (notFound) {
    return (
      <EmptyState
        icon={<FileCode2 className="h-8 w-8 text-text-faint" />}
        title="Repository not found"
        description={
          error ?? "This repository isn't in RepoLens. It may have been removed."
        }
        action={
          <Button onClick={() => navigate("/")}>
            <ArrowLeft className="h-4 w-4" />
            Analyze a repository
          </Button>
        }
      />
    );
  }

  if (!repo) {
    return (
      <EmptyState
        icon={<CircleDot className="h-8 w-8 animate-pulse text-accent-hover" />}
        title="Loading repository…"
        description="Fetching the ingested repository metadata."
      />
    );
  }

  const languages = Object.entries(repo.languages).sort((a, b) => b[1] - a[1]);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-10 px-6 py-10">
      {/* Repository identity */}
      <section className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-semibold tracking-tight text-text-primary">
              {repo.owner} <span className="text-text-faint">/</span> {repo.name}
            </h1>
            <Badge tone={STATUS_TONE[repo.status]}>{repo.status}</Badge>
          </div>
          <p className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-text-secondary">
            <a
              href={repo.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-text-secondary transition-colors hover:text-accent-hover"
            >
              {repo.url}
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
            {repo.branch && (
              <span className="inline-flex items-center gap-1">
                <GitBranch className="h-3.5 w-3.5 text-text-faint" />
                {repo.branch}
              </span>
            )}
            {repo.commit_sha && (
              <span className="inline-flex items-center gap-1 font-mono">
                <GitCommitHorizontal className="h-3.5 w-3.5 text-text-faint" />
                {shortSha(repo.commit_sha)}
              </span>
            )}
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={() => navigate("/")}>
          <ArrowLeft className="h-4 w-4" />
          Back
        </Button>
      </section>

      {repo.status === "failed" && (
        <div className="flex items-start gap-3 rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {repo.error_message ?? "Ingestion failed."} This repository was not
            indexed and can be removed.
          </span>
        </div>
      )}

      {/* Primary actions */}
      <section className="flex flex-col gap-3">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-text-faint">
          What do you want to do?
        </h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <ActionCard
            to={`/repo/${repo.id}/investigate`}
            icon={<Search className="h-4 w-4" />}
            title="Explore Code"
            description="Find any function, class, or file — and see where it's used."
          />
          <ActionCard
            to={`/repo/${repo.id}/changes`}
            icon={<GitCompare className="h-4 w-4" />}
            title="See What Changed"
            description="Start with the latest commit and what it changed."
          />
          <ActionCard
            to={`/repo/${repo.id}/impact`}
            icon={<Network className="h-4 w-4" />}
            title="See What Might Be Affected"
            description="Trace how the change could ripple through the codebase."
          />
          <ActionCard
            to={`/repo/${repo.id}/review`}
            icon={<ClipboardCheck className="h-4 w-4" />}
            title="Review Before Merging"
            description="Walk the generated checklist before you merge."
          />
        </div>
      </section>

      {/* Repository snapshot */}
      {repo.status === "ready" && (
        <section className="flex flex-col gap-3">
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-text-faint">
            Repository snapshot
          </h2>
          <div className="flex flex-col gap-6 rounded-lg border border-line bg-surface px-6 py-5 sm:flex-row sm:items-start sm:gap-12">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-text-faint">
                Files
              </p>
              <p className="mt-1 text-2xl font-semibold tracking-tight text-text-primary">
                {formatNumber(repo.file_count)}
              </p>
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-text-faint">
                Languages
              </p>
              <p className="mt-1 text-2xl font-semibold tracking-tight text-text-primary">
                {languages.length === 0
                  ? "None"
                  : languages
                      .map(([name, pct]) => `${name} ${pct.toFixed(0)}%`)
                      .join(" · ")}
              </p>
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-text-faint">
                Relationships
              </p>
              <p className="mt-1 text-2xl font-semibold tracking-tight text-text-primary">
                {repo.relationship_count > 0
                  ? formatNumber(repo.relationship_count)
                  : "—"}
              </p>
            </div>
          </div>
        </section>
      )}

      {/* Recent activity */}
      <section className="flex flex-col gap-3">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-text-faint">
          Recent activity
        </h2>
        <RecentActivity repositoryId={repo.id} />
      </section>

      <p className="text-center text-xs text-text-faint">
        RepoLens inspected the repository source without executing it.
      </p>
    </div>
  );
}