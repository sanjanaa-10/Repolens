import { useEffect, useMemo, useState } from "react";
import {
  ArrowUpDown,
  ChevronRight,
  CornerDownLeft,
  FileCode2,
} from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { getRepository } from "../api/client";
import { SourceView } from "../components/analysis/SourceView";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { Input } from "../components/ui/Input";
import type { RepositoryInfo } from "../types";

export function SourcePage() {
  const { id } = useParams<{ id: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawPath = searchParams.get("path") ?? "";
  const filePath = rawPath ? decodeURIComponent(rawPath) : null;
  const rawLine = searchParams.get("line");
  const line = rawLine ? Math.max(1, Number(rawLine) || 1) : null;
  const rawRange = searchParams.get("range");
  const highlightRange = useMemo(() => {
    if (!rawRange) return null;
    const [a, b] = rawRange.split(":").map((v) => Number(v));
    if (!a || !b || b < a) return null;
    return { start: a, end: b };
  }, [rawRange]);

  const [repo, setRepo] = useState<RepositoryInfo | null>(null);
  const [jump, setJump] = useState("");

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
          icon={<FileCode2 className="h-8 w-8 animate-pulse text-accent-hover" />}
          title="Loading repository…"
          description="Fetching repository metadata to view source."
        />
      </div>
    );
  }

  const goToLine = () => {
    const target = Math.max(1, Number(jump) || 0);
    if (!target) return;
    const next = new URLSearchParams(searchParams);
    next.set("line", String(target));
    setSearchParams(next, { replace: true });
    setJump("");
  };

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
        <span className="text-accent-hover">Source</span>
        {filePath && (
          <>
            <ChevronRight className="h-3 w-3 text-text-faint" />
            <span className="truncate font-mono text-text-secondary">
              {filePath}
            </span>
          </>
        )}
        <div className="ml-auto flex items-center gap-2">
          <form
            className="flex items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              goToLine();
            }}
          >
            <span className="flex items-center gap-1 text-text-faint">
              <ArrowUpDown className="h-3 w-3" />
              Line
            </span>
            <Input
              className="h-7 w-20 font-mono text-xs"
              value={jump}
              onChange={(e) => setJump(e.target.value)}
              placeholder={line ? String(line) : "n"}
              spellCheck={false}
            />
            <Button type="submit" variant="ghost" size="sm" aria-label="Go to line">
              <CornerDownLeft className="h-3.5 w-3.5" />
            </Button>
          </form>
        </div>
      </div>
      <div className="flex-1 overflow-hidden">
        <SourceView
          repositoryId={repo.id}
          filePath={filePath}
          highlightLine={line}
          highlightRange={highlightRange}
        />
      </div>
    </div>
  );
}