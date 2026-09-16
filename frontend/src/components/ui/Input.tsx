import { forwardRef } from "react";
import { cn } from "../../lib/utils";

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, hint, error, className, ...props }, ref) => (
    <div className="flex flex-col gap-1.5 w-full">
      {label && (
        <label className="text-xs font-medium text-text-secondary tracking-wide">
          {label}
        </label>
      )}
      <input
        ref={ref}
        className={cn(
          "h-10 w-full rounded-md border border-line bg-ink-800 px-3.5 text-sm text-text-primary",
          "placeholder:text-text-faint transition-colors duration-150",
          "hover:border-ink-300",
          "focus:border-accent focus:ring-1 focus:ring-accent outline-none",
          error && "border-danger/60 focus:border-danger focus:ring-danger",
          className
        )}
        {...props}
      />
      {hint && !error && (
        <p className="text-xs text-text-faint">{hint}</p>
      )}
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  )
);
Input.displayName = "Input";