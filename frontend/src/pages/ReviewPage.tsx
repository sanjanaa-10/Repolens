import { useEffect, useState } from "react";
import { ChevronRight, ClipboardCheck } from "lucide-react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getRepository } from "../api/client";
import { ReviewView } from "../components/analysis/ReviewView";
import { EmptyState } from "../components/ui/EmptyState";
import type { RepositoryInfo } from "../types";

export function ReviewPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
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
          icon={<ClipboardCheck className="h-8 w-8 animate-pulse text-accent-hover" />}
          title="Loading repository…"
          description="Fetching repository metadata before loading the review."
        />
      </div>
    );
  }

  const repositoryId = repo.id;
  const reviewParam = searchParams.get("review");
  const analysisParam = searchParams.get("analysis");

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-line bg-ink-900 px-4 text-xs text-text-muted">
        <Link to="/" className="hover:text-text-primary">
          RepoLens
        </Link>
        <ChevronRight className="h-3 w-3 text-text-faint" />
        <Link
          to={`/repo/${repositoryId}`}
          className="font-medium text-text-secondary hover:text-text-primary"
        >
          {repo.owner}/{repo.name}
        </Link>
        <ChevronRight className="h-3 w-3 text-text-faint" />
        <span className="text-accent-hover">Before you merge</span>
      </div>
      <ReviewView
        repositoryId={repositoryId}
        reviewId={reviewParam ? Number(reviewParam) : null}
        analysisId={analysisParam ? Number(analysisParam) : null}
        onReviewCreated={(reviewId) => {
          const params = new URLSearchParams(searchParams);
          params.set("review", String(reviewId));
          navigate(
            `/repo/${repositoryId}/review?${params.toString()}`,
            { replace: true }
          );
        }}
      />
    </div>
  );
}