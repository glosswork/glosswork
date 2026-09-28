/** Empty-state block: what there is none of, why, and — when one exists —
 * the action that changes that. */
import type { ReactNode } from "react";
import { cx } from "./cx";

export interface EmptyStateProps {
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  /**
   * Pass `false` when this sits inside an already-bordered container, so the screen does not
   * render a card inside a card. It is an explicit prop and not a CSS descendant rule on
   * purpose — an appearance that changes silently based on ancestry is action-at-a-distance,
   * the kind that nests cards unnoticed. The primitive itself cannot know what contains it, so
   * the caller says.
   */
  bordered?: boolean;
  "data-testid"?: string;
}

export function EmptyState({ title, children, action, bordered = true, ...rest }: EmptyStateProps) {
  return (
    <div
      className={cx(
        "max-w-lg px-5 py-7 text-center text-base text-ink-2",
        bordered && "rounded-card border border-dashed border-line-2",
      )}
      {...rest}
    >
      <p className="font-semibold text-ink">{title}</p>
      {children && <p className="mt-1">{children}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
