import { useEffect, useState } from "react";
import { ChevronRight, Network } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { getRepository } from "../api/client";
import { ImpactView } from "../components/analysis/ImpactView";
import { EmptyState } from "../components/ui/EmptyState";
import type { RepositoryInfo } from "../types";

export function ImpactPage() {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const [repo, setRepo] = useState<RepositoryInfo | null>(null);

  useEffect(() => {
    let active = true;
    if (!id) return;
    getRepository(Number(id))
      .then((data) => {
        if (active) setRepo(data);
      })
      .catch(() => {
        if (active) setRepo(null);
      });
    return () => {
      active = false;
    };
  }, [id]);

  if (!repo) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState
          icon={<Network className="h-8 w-8 animate-pulse text-accent-hover" />}
          title="Loading repository…"
          description="Fetching repository metadata before impact simulation."
        />
      </div>
    );
  }

  const diffParam = searchParams.get("diff");
  const baseParam = searchParams.get("base");
  const headParam = searchParams.get("head");
  const analysisParam = searchParams.get("analysis");

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-line bg-ink-900 px-4 text-xs text-text-muted">
        <Link to="/" className="hover:text-text-primary">
          RepoLens
        </Link>
        <ChevronRight className="h-3 w-3 text-text-faint" />
        <Link
          to={`/repo/${repo.id}`}
          className="font-medium text-text-secondary hover:text-text-primary"
        >
          {repo.owner}/{repo.name}
        </Link>
        <ChevronRight className="h-3 w-3 text-text-faint" />
        <span className="text-accent-hover">What might be affected</span>
      </div>
      <ImpactView
        repositoryId={repo.id}
        initialDiffId={diffParam ? Number(diffParam) : null}
        initialBase={baseParam ?? undefined}
        initialHead={headParam ?? undefined}
        initialAnalysisId={analysisParam ? Number(analysisParam) : null}
      />
    </div>
  );
}