import { NavLink as RouterNavLink, Outlet, useNavigate, useParams } from "react-router-dom";
import { Search, GitBranch, CircleDot } from "lucide-react";
import { Logo } from "../ui/Logo";
import { cn } from "../../lib/utils";
import { useRepository } from "../../hooks/useRepository";
import type { AnalysisStatus } from "../../types";

const navItems = [
  { to: "", label: "Overview", end: true, num: 1 },
  { to: "investigate", label: "Investigate", num: 2 },
  { to: "changes", label: "Changes", num: 3 },
  { to: "impact", label: "Impact", num: 4 },
  { to: "review", label: "Review", num: 5 },
];

const STATUS_LABEL: Record<AnalysisStatus, string> = {
  pending: "Pending",
  cloning: "Cloning repositoryâ€¦",
  indexing: "Indexing filesâ€¦",
  ready: "Indexed and ready",
  failed: "Ingestion failed",
};

export function AppShell() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { repo } = useRepository(id);

  return (
    <div className="flex h-screen flex-col bg-ink">
      {/* Top bar */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-line bg-ink-900 px-4">
        <div className="flex items-center gap-3">
          <Logo />
          <div className="flex items-center gap-1.5 text-sm text-text-secondary">
            <CircleDot className="h-3.5 w-3.5 text-accent-hover" />
            <span className="font-medium text-text-primary">
              {repo ? `${repo.owner}/${repo.name}` : "repository"}
            </span>
            <span className="text-text-muted">/</span>
            <GitBranch className="h-3.5 w-3.5 text-text-muted" />
            <span className="text-text-muted">{repo?.branch ?? "â€”"}</span>
          </div>
        </div>
        <button
          type="button"
          onClick={() => navigate(`/repo/${id}/investigate`)}
          className="group flex h-8 items-center gap-2 rounded-md border border-line bg-ink-700 px-3 text-xs text-text-muted transition-colors hover:border-ink-300 hover:text-text-secondary"
          aria-label="Search"
        >
          <Search className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Search code</span>
          <kbd className="ml-2 hidden rounded border border-line bg-ink-900 px-1.5 py-0.5 font-mono text-[10px] text-text-faint sm:inline">
            âŒ˜K
          </kbd>
        </button>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-ink-900">
          <nav className="flex flex-col gap-0.5 p-3" aria-label="Primary">
            {navItems.map((item) => (
              <RouterNavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(
                    "rounded-md px-3 py-2 text-sm transition-colors",
                    isActive
                      ? "bg-accent/15 font-medium text-accent-hover"
                      : "text-text-secondary hover:bg-ink-700 hover:text-text-primary"
                  )
                }
              >
                <span className="mr-2 inline-flex h-4 w-4 items-center justify-center rounded-full bg-ink-700 text-[10px] tabular-nums text-text-faint">
                  {item.num}
                </span              >
                {item.num !== undefined && (
                  <span className="mr-2.5 inline-flex h-4 w-4 items-center justify-center rounded-full bg-ink-700 text-[10px] font-medium tabular-nums text-text-secondary">
                    {item.num}
                  </span>
                )}
                {item.label}
              </RouterNavLink>
            ))}
          </nav>

          <div className="mt-auto border-t border-line p-4">
            <p className="text-[11px] uppercase tracking-wider text-text-faint">
              Analysis
            </p>
            <p className="mt-1 text-xs text-text-secondary">
              {repo
                ? STATUS_LABEL[repo.status]
                : "Loading repository statusâ€¦"}
            </p>
            {repo && repo.status === "ready" && (
              <p className="mt-1 text-[11px] text-text-faint">
                {repo.file_count.toLocaleString()} files indexed from{" "}
                {repo.branch ?? "default branch"}.
              </p>
            )}
            {repo && repo.status === "failed" && (
              <p className="mt-1 text-[11px] text-danger">
                No index available for this repository.
              </p>
            )}
          </div>
        </aside>

        {/* Main workspace */}
        <main className="flex-1 overflow-auto bg-ink">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
