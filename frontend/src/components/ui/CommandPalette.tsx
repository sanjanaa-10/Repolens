import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type React from "react";
import {
  AnimatePresence,
  motion,
  useReducedMotion,
} from "framer-motion";
import {
  ClipboardCheck,
  FolderGit2,
  GitCompare,
  Home,
  Network,
  Search,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { getRepositories } from "../../api/client";
import { cn } from "../../lib/utils";

interface CommandItem {
  id: string;
  label: string;
  hint?: string;
  icon: React.ReactNode;
  run: () => void;
}

export function CommandPalette() {
  const navigate = useNavigate();
  const reduce = useReducedMotion();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const [repos, setRepos] = useState<{ id: number; name: string; owner: string }[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setSelected(0);
    inputRef.current?.focus();
    getRepositories()
      .then((list) =>
        setRepos(
          list.map((r) => ({ id: r.id, name: r.name, owner: r.owner }))
        )
      )
      .catch(() => setRepos([]));
  }, [open]);

  const repoMatch = useMemo(
    () => window.location.pathname.match(/\/repo\/(\d+)/),
    [open]
  );
  const repoId = repoMatch ? Number(repoMatch[1]) : null;

  const baseItems = useMemo<CommandItem[]>(() => {
    const home: CommandItem = {
      id: "home",
      label: "Analyze a repository",
      hint: "Back to landing",
      icon: <Home className="h-4 w-4 text-text-muted" />,
      run: () => {
        setOpen(false);
        navigate("/");
      },
    };
    if (!repoId) return [home];
    return [
      home,
      {
        id: "overview",
        label: "Repository overview",
        hint: `Repository ${repoId}`,
        icon: <FolderGit2 className="h-4 w-4 text-text-muted" />,
        run: () => {
          setOpen(false);
          navigate(`/repo/${repoId}`);
        },
      },
      {
        id: "investigate",
        label: "Explore code",
        hint: "Search and trace relationships",
        icon: <Search className="h-4 w-4 text-text-muted" />,
        run: () => {
          setOpen(false);
          navigate(`/repo/${repoId}/investigate`);
        },
      },
      {
        id: "changes",
        label: "Changes",
        hint: "Diff between revisions",
        icon: <GitCompare className="h-4 w-4 text-text-muted" />,
        run: () => {
          setOpen(false);
          navigate(`/repo/${repoId}/changes`);
        },
      },
      {
        id: "impact",
        label: "What might be affected?",
        hint: "Ripple through the codebase",
        icon: <Network className="h-4 w-4 text-text-muted" />,
        run: () => {
          setOpen(false);
          navigate(`/repo/${repoId}/impact`);
        },
      },
      {
        id: "review",
        label: "Before you merge",
        hint: "Deterministic checklist",
        icon: <ClipboardCheck className="h-4 w-4 text-text-muted" />,
        run: () => {
          setOpen(false);
          navigate(`/repo/${repoId}/review`);
        },
      },
    ];
  }, [repoId, navigate]);

  const items = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = [...baseItems];
    for (const repo of repos) {
      all.push({
        id: `repo-${repo.id}`,
        label: `${repo.owner}/${repo.name}`,
        hint: "Open repository",
        icon: <FolderGit2 className="h-4 w-4 text-text-muted" />,
        run: () => {
          setOpen(false);
          navigate(`/repo/${repo.id}`);
        },
      });
    }
    if (!q) return all;
    return all.filter(
      (item) =>
        item.label.toLowerCase().includes(q) ||
        (item.hint ?? "").toLowerCase().includes(q)
    );
  }, [baseItems, repos, query, navigate]);

  // Reset selection when the filtered list shrinks.
  useEffect(() => {
    setSelected((s) => Math.min(s, Math.max(0, items.length - 1)));
  }, [items.length]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelected((s) => Math.min(s + 1, items.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelected((s) => Math.max(s - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const item = items[selected];
        if (item) item.run();
      } else if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
      }
    },
    [items, selected]
  );

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          role="dialog"
          aria-modal="true"
          aria-label="Command palette"
          className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[15vh]"
          initial={reduce ? { opacity: 1 } : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
        >
          <button
            type="button"
            aria-label="Close command palette"
            onClick={() => setOpen(false)}
            className="absolute inset-0 h-full w-full cursor-default bg-black/50"
          />
          <motion.div
            onKeyDown={onKeyDown}
            tabIndex={-1}
            className="relative w-full max-w-xl overflow-hidden rounded-xl border border-line bg-ink-900 shadow-panel outline-none"
            initial={reduce ? { y: 0 } : { y: -8, opacity: 0.6 }}
            animate={{ y: 0, opacity: 1 }}
            exit={reduce ? { opacity: 0 } : { y: -8, opacity: 0 }}
            transition={{ type: "tween", duration: 0.16 }}
          >
            <div className="flex items-center gap-2 border-b border-line px-4">
              <Search className="h-4 w-4 shrink-0 text-text-faint" />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search commands and repositories…"
                className="h-12 w-full bg-transparent text-sm text-text-primary outline-none placeholder:text-text-faint"
              />
              <kbd className="shrink-0 rounded border border-line bg-ink-800 px-1.5 py-0.5 font-mono text-[10px] text-text-faint">
                ESC
              </kbd>
            </div>
            <div className="max-h-80 overflow-y-auto py-1">
              {items.length === 0 && (
                <p className="px-4 py-6 text-center text-sm text-text-muted">
                  No commands or repositories match “{query}”.
                </p>
              )}
              {items.map((item, i) => (
                <button
                  key={item.id}
                  type="button"
                  onMouseEnter={() => setSelected(i)}
                  onClick={item.run}
                  className={cn(
                    "flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors",
                    i === selected ? "bg-ink-700/60" : "bg-transparent"
                  )}
                >
                  <span className="shrink-0">{item.icon}</span>
                  <span className="min-w-0 flex-1 truncate text-sm text-text-primary">
                    {item.label}
                  </span>
                  {item.hint && (
                    <span className="shrink-0 text-[11px] text-text-faint">
                      {item.hint}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}