import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion, useReducedMotion } from "framer-motion";
import {
  Search,
  GitBranch,
  Workflow,
  CheckCircle2,
  Compass,
  Hammer,
  ListChecks,
  ArrowRight,
  Loader2,
  AlertCircle,
  Github,
} from "lucide-react";
import { Logo } from "../components/ui/Logo";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { cn } from "../lib/utils";
import { getRepositories, ingestRepository } from "../api/client";

const INGEST_STEPS = ["Cloning repository…", "Indexing source files…"];

function isValidGithubUrl(raw: string): boolean {
  const trimmed = raw.trim();
  if (!trimmed) return false;
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return false;
  const host = parsed.hostname.toLowerCase();
  if (host !== "github.com" && !host.endsWith(".github.com")) return false;
  const parts = parsed.pathname.split("/").filter(Boolean);
  return parts.length >= 2;
}

// ---------- Background relationship visualization ----------

interface GraphNode {
  id: string;
  label: string;
  x: number;
  y: number;
  kind: "module" | "class" | "function" | "repo";
}

interface GraphEdge {
  from: string;
  to: string;
}

const HERO_GRAPH: { nodes: GraphNode[]; edges: GraphEdge[] } = {
  nodes: [
    { id: "repo", label: "auth-service", x: 50, y: 8, kind: "repo" },
    { id: "controller", label: "AuthController", x: 20, y: 40, kind: "class" },
    { id: "service", label: "AuthService", x: 50, y: 40, kind: "class" },
    { id: "token", label: "TokenService", x: 78, y: 48, kind: "class" },
    { id: "jwt", label: "JWT", x: 92, y: 72, kind: "module" },
    { id: "repo2", label: "UserRepository", x: 35, y: 72, kind: "class" },
    { id: "db", label: "DB", x: 70, y: 88, kind: "module" },
  ],
  edges: [
    { from: "repo", to: "controller" },
    { from: "repo", to: "service" },
    { from: "controller", to: "service" },
    { from: "service", to: "token" },
    { from: "service", to: "repo2" },
    { from: "token", to: "jwt" },
    { from: "repo2", to: "db" },
  ],
};

function HeroVisual({ dimmed }: { dimmed: boolean }) {
  const reduce = useReducedMotion();
  return (
    <svg
      viewBox="0 0 100 100"
      className={cn(
        "h-full w-full transition-opacity duration-700",
        dimmed ? "opacity-10" : "opacity-40"
      )}
      aria-hidden="true"
      preserveAspectRatio="xMidYMid meet"
    >
      {HERO_GRAPH.edges.map((e, i) => {
        const a = HERO_GRAPH.nodes.find((n) => n.id === e.from)!;
        const b = HERO_GRAPH.nodes.find((n) => n.id === e.to)!;
        return (
          <motion.line
            key={i}
            x1={a.x}
            y1={a.y}
            x2={b.x}
            y2={b.y}
            stroke={reduce ? "#22222e" : "#6366f1"}
            strokeOpacity={reduce ? 0.2 : 0.18}
            strokeWidth="0.35"
            strokeDasharray="1.5 1.5"
            initial={reduce ? undefined : { opacity: 0 }}
            animate={reduce ? undefined : { opacity: [0.1, 0.45, 0.1] }}
            transition={
              reduce
                ? undefined
                : { duration: 8 + i * 1.5, repeat: Infinity, ease: "easeInOut" }
            }
          />
        );
      })}
      {HERO_GRAPH.nodes.map((n) => (
        <g key={n.id}>
          <motion.circle
            cx={n.x}
            cy={n.y}
            r={n.kind === "repo" ? 1.8 : n.kind === "class" ? 1.3 : 1.0}
            fill={n.kind === "repo" ? "#6366f1" : "#16161f"}
            stroke={n.kind === "repo" ? "#818cf8" : "#2a2a38"}
            strokeWidth="0.3"
            initial={reduce ? undefined : { opacity: 0.3 }}
            animate={reduce ? undefined : { opacity: [0.3, 0.7, 0.3] }}
            transition={
              reduce
                ? undefined
                : {
                    duration: 6 + n.y / 8,
                    repeat: Infinity,
                    ease: "easeInOut",
                  }
            }
          />
          {/* Only the top repo node gets a visible label; others stay minimal */}
          {n.kind === "repo" && (
            <text
              x={n.x}
              y={n.y - 3.2}
              textAnchor="middle"
              fontSize="2.2"
              fill="#6366f1"
              fontFamily="monospace"
              opacity={0.6}
            >
              {n.label}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}

// ---------- Landing page ----------

export function LandingPage() {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [dimHero, setDimHero] = useState(false);
  const [step, setStep] = useState(0);
  const [firstRepoId, setFirstRepoId] = useState<number | null>(null);
  const [reposLoaded, setReposLoaded] = useState(false);
  const stepTimer = useRef<number | null>(null);
  const reduce = useReducedMotion();

  // Discover an already-analyzed repository for the secondary “View Demo” action.
  useEffect(() => {
    let active = true;
    getRepositories()
      .then((repos) => {
        if (active && repos.length > 0) setFirstRepoId(repos[0].id);
        if (active) setReposLoaded(true);
      })
      .catch(() => {
        if (active) setReposLoaded(true);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!loading) return;
    stepTimer.current = window.setInterval(
      () => setStep((s) => Math.min(s + 1, INGEST_STEPS.length - 1)),
      8000
    );
    return () => {
      if (stepTimer.current !== null) window.clearInterval(stepTimer.current);
    };
  }, [loading]);

  const featureBlocks = useMemo(
    () => [
      {
        icon: Compass,
        index: "01",
        title: "Find",
        body: "Locate exactly where a feature is implemented across unfamiliar code.",
      },
      {
        icon: Workflow,
        index: "02",
        title: "Explore",
        body: "Follow real calls, imports and references with source-level evidence.",
      },
      {
        icon: Hammer,
        index: "03",
        title: "Simulate",
        body: "Trace how a change to one symbol could ripple through the codebase.",
      },
      {
        icon: ListChecks,
        index: "04",
        title: "Review",
        body: "Get an actionable checklist that tells you what to inspect before merging.",
      },
    ],
    []
  );

  async function handleAnalyze(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) {
      setError("Paste a public GitHub repository URL first.");
      return;
    }
    if (!isValidGithubUrl(url)) {
      setError(
        "Enter a GitHub repository URL, e.g. https://github.com/pallets/itsdangerous"
      );
      return;
    }
    setError(null);
    setLoading(true);
    setStep(0);
    setDimHero(true);
    try {
      const repo = await ingestRepository(url.trim());
      navigate(`/repo/${repo.id}`);
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "We couldn't access this repository.";
      setError(
        typeof detail === "string"
          ? detail
          : "We couldn't access this repository."
      );
      setDimHero(false);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-ink text-text-primary">
      {/* Nav */}
      <header className="fixed inset-x-0 top-0 z-40 flex h-16 items-center justify-between border-b border-line/60 bg-ink/80 px-5 backdrop-blur-sm">
        <Logo />
        <span className="hidden text-xs text-text-muted sm:block">
          Understand the code before you change it.
        </span>
        <a
          href="#features"
          className="text-sm text-text-secondary transition-colors hover:text-text-primary"
        >
          How it works
        </a>
      </header>

      {/* Hero */}
      <section className="relative flex min-h-[86vh] flex-col items-center justify-center overflow-hidden px-6 pt-24 pb-14">
        <div className="bg-grid bg-grid-fade absolute inset-0" aria-hidden="true" />

        {/* Circulating relationship graph behind hero — masked to stay clear of the headline */}
        <div
          className="pointer-events-none absolute inset-0 flex items-center justify-center"
          aria-hidden="true"
          style={{
            maskImage:
              "radial-gradient(ellipse 60% 55% at 50% 40%, transparent 0%, black 60%)",
            WebkitMaskImage:
              "radial-gradient(ellipse 60% 55% at 50% 40%, transparent 0%, black 60%)",
          }}
        >
          <div className="relative h-[48vh] w-full max-w-2xl translate-y-8">
            <HeroVisual dimmed={dimHero} />
          </div>
        </div>

        <motion.div
          className="relative z-10 flex w-full max-w-3xl flex-col items-center text-center"
          initial={reduce ? undefined : { opacity: 0, y: 16 }}
          animate={reduce ? undefined : { opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
        >
          <h1 className="text-balance text-4xl font-semibold leading-[1.1] tracking-tight sm:text-5xl md:text-6xl">
            Understand the impact of your code changes.
          </h1>

          <p className="mx-auto mt-5 max-w-xl text-pretty text-base leading-relaxed text-text-secondary sm:text-lg">
            RepoLens maps real code relationships inside your repository to
            reveal how a change could ripple through before you touch it.
          </p>

          {/* URL form */}
          <form
            onSubmit={handleAnalyze}
            className="mt-10 w-full max-w-2xl"
            aria-label="Analyze a repository"
          >
            <div className="flex flex-col gap-3 sm:flex-row">
              <div className="relative flex-1">
                <Github className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-faint" />
                <Input
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://github.com/pallets/itsdangerous"
                  aria-label="GitHub repository URL"
                  className="h-12 pl-10 text-sm"
                />
              </div>
              <Button
              type="submit"
              size="lg"
              isLoading={loading}
              disabled={loading}
              className="shrink-0"
            >
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {INGEST_STEPS[step]}
                </>
              ) : (
                <>
                  Analyze Repository
                  <ArrowRight className="h-4 w-4" />
                </>
              )}
            </Button>
            {firstRepoId ? (
              <Button
                type="button"
                size="lg"
                variant="secondary"
                className="shrink-0"
                onClick={() => navigate(`/repo/${firstRepoId}`)}
              >
                Try Demo
                <ArrowRight className="h-4 w-4" />
              </Button>
            ) : (
              <Button
                type="button"
                size="lg"
                variant="ghost"
                disabled
                aria-disabled
                title={
                  reposLoaded
                    ? "Analyze a repository first — none is stored yet."
                    : "Checking for an analyzed repository…"
                }
                className="shrink-0"
              >
                Try Demo
                <ArrowRight className="h-4 w-4" />
              </Button>
            )}
          </div>
            {error && (
              <motion.p
                initial={reduce ? undefined : { opacity: 0 }}
                animate={{ opacity: 1 }}
                className="mt-3 flex items-center justify-center gap-2 text-sm text-danger"
              >
                <AlertCircle className="h-4 w-4" />
                {error}
              </motion.p>
            )}
            <p className="mt-3 text-xs text-text-faint">
              Static analysis · Python · JavaScript · TypeScript · No repository
              code execution
            </p>
          </form>
        </motion.div>
      </section>

      {/* Feature sections */}
      <section id="features" className="relative border-t border-line/60">
        <div className="mx-auto max-w-6xl px-6 py-24">
          <div className="grid gap-x-10 gap-y-14 sm:grid-cols-2">
            {featureBlocks.map((f, i) => (
              <motion.div
                key={f.title}
                initial={reduce ? undefined : { opacity: 0, y: 24 }}
                whileInView={reduce ? undefined : { opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-80px" }}
                transition={{ duration: 0.6, delay: i * 0.08, ease: [0.16, 1, 0.3, 1] }}
                className="group flex gap-5"
              >
                <div className="flex flex-col items-center">
                  <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-line bg-ink-800 text-accent-hover transition-colors group-hover:border-accent/40">
                    <f.icon className="h-5 w-5" />
                  </span>
                  <span className="mt-3 h-full w-px bg-line" aria-hidden="true" />
                </div>
                <div>
                  <p className="font-mono text-xs text-text-faint">{f.index}</p>
                  <h2 className="mt-1 text-2xl font-medium tracking-tight text-text-primary">
                    {f.title}
                  </h2>
                  <p className="mt-2 max-w-sm text-sm leading-relaxed text-text-secondary">
                    {f.body}
                  </p>
                </div>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Workflow strip */}
      <section className="border-t border-line/60">
        <div className="mx-auto max-w-6xl px-6 py-16">
          <div className="flex flex-col items-center gap-3 text-center">
            <p className="text-sm text-text-muted">
              INDEX → INVESTIGATE → SIMULATE → REVIEW
            </p>
            <p className="max-w-xl text-balance text-xl text-text-secondary">
              One workflow from unfamiliar repository to a confident, reviewed
              change.
            </p>
            <CheckCircle2 className="mt-2 h-8 w-8 text-accent-hover" />
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-line/60">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-10 sm:flex-row">
          <Logo />
          <p className="flex items-center gap-1.5 text-xs text-text-muted">
            <Search className="h-3.5 w-3.5" />
            <GitBranch className="h-3.5 w-3.5" />
            A precision instrument for understanding software.
          </p>
        </div>
      </footer>
    </div>
  );
}