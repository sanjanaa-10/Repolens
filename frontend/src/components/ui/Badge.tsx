import { cn } from "../../lib/utils";

type BadgeTone =
  | "neutral"
  | "accent"
  | "success"
  | "warning"
  | "danger"
  | "muted";

interface BadgeProps {
  tone?: BadgeTone;
  className?: string;
  children: React.ReactNode;
}

const tones: Record<BadgeTone, string> = {
  neutral: "bg-ink-500 text-text-secondary border-line",
  accent: "bg-accent/15 text-accent-hover border-accent/30",
  success: "bg-success/12 text-success border-success/25",
  warning: "bg-warning/12 text-warning border-warning/25",
  danger: "bg-danger/12 text-danger border-danger/25",
  muted: "bg-ink-600 text-text-faint border-line",
};

export function Badge({ tone = "neutral", className, children }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider border",
        tones[tone],
        className
      )}
    >
      {children}
    </span>
  );
}

export function ConfidenceBadge({ value }: { value: string }) {
  const tone =
    value === "DIRECT"
      ? "success"
      : value === "INFERRED"
        ? "accent"
        : value === "EXTERNAL"
          ? "muted"
          : "warning";
  return <Badge tone={tone}>{value}</Badge>;
}