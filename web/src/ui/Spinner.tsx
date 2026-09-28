/**
 * Loading indicator. Defaults its text to the literal `Loading...` the
 * existing screens render and their tests query, so adopting it is a class change, not a
 * copy change.
 */
export interface SpinnerProps {
  label?: string;
}

export function Spinner({ label = "Loading..." }: SpinnerProps) {
  return (
    <p role="status" className="flex items-center gap-2 text-base text-ink-2">
      <span
        aria-hidden="true"
        className="size-4 animate-spin rounded-full border-2 border-line-2 border-t-human"
      />
      {label}
    </p>
  );
}
