import { Logo } from "../components/ui/Logo";

export function WorkInProgress({ title }: { title: string }) {
  return (
    <div className="flex h-full min-h-[60vh] flex-col items-center justify-center gap-4 px-6 text-center">
      <Logo className="opacity-80" />
      <div>
        <p className="text-lg font-medium text-text-primary">{title}</p>
        <p className="mt-1 max-w-sm text-sm text-text-muted">
          The page you are looking for does not exist. Use the command palette
          or the navigation to explore an indexed repository.
        </p>
      </div>
    </div>
  );
}