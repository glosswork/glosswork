/**
 * Status/scope chip. Semantic tones are separate from the accent (DD-41):
 * `accent` marks the caller's own privileged things (an admin scope), the semantic tones
 * mark user data (statuses, health), `neutral` is the quiet default.
 */
import type { ComponentPropsWithoutRef } from "react";
import { cx } from "./cx";

export type BadgeTone = "accent" | "success" | "danger" | "warning" | "info" | "neutral";

const TONE_CLASSES: Record<BadgeTone, string> = {
  accent: "border-human-line bg-human-soft text-human-ink",
  success: "border-ok-line bg-ok-soft text-ok",
  danger: "border-bad-line bg-bad-soft text-bad",
  warning: "border-warn-line bg-warn-soft text-warn",
  info: "border-line bg-sunk text-ink-2",
  neutral: "border-line bg-ground text-ink-2",
};

export interface BadgeProps extends ComponentPropsWithoutRef<"span"> {
  tone?: BadgeTone;
}

export function Badge({ tone = "neutral", className, ...rest }: BadgeProps) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-card border px-2 py-0.5 text-xs font-semibold",
        TONE_CLASSES[tone],
        className,
      )}
      {...rest}
    />
  );
}
