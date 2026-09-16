import { cn } from "../../lib/utils";

/** RepoLens mark: a minimal geometric lens merged with a code-bracket motif. */
export function Logo({
  className,
  withWordmark = true,
}: {
  className?: string;
  withWordmark?: boolean;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <svg viewBox="0 0 64 64" className="h-6 w-6 shrink-0" fill="none" aria-hidden="true">
        <defs>
          <linearGradient id="rl-g" x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="#6366f1" />
            <stop offset="1" stopColor="#818cf8" />
          </linearGradient>
        </defs>
        <path d="M17 22 L9 32 L17 42" stroke="#a3a3b0" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M47 22 L55 32 L47 42" stroke="#a3a3b0" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="32" cy="32" r="13" fill="url(#rl-g)" />
      </svg>
      {withWordmark && (
        <span className="font-semibold tracking-tight text-text-primary text-[15px]">
          Repo<span className="text-accent-hover">Lens</span>
        </span>
      )}
    </span>
  );
}