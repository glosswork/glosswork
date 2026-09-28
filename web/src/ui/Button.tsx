/**
 * The one button (DD-41). Renders exactly the `<button>` it
 * replaces — no wrapper, every native prop passes through — so swapping a raw `<button>`
 * for `<Button>` changes classes and nothing the test suite queries.
 */
import type { ComponentPropsWithoutRef } from "react";
import { cx } from "./cx";

export type ButtonVariant = "primary" | "secondary" | "danger" | "quiet";

const BASE_CLASS =
  "cursor-pointer rounded-ctl border px-3.5 py-1.5 text-base font-medium transition-colors " +
  "disabled:cursor-not-allowed disabled:opacity-45";

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: "border-transparent bg-human text-on-human hover:bg-human-ink",
  secondary: "border-line-2 bg-surface text-ink hover:bg-ground",
  danger: "border-bad-line bg-surface text-bad hover:bg-bad-soft",
  quiet: "border-transparent bg-transparent text-ink-2 hover:bg-ground hover:text-ink",
};

export interface ButtonProps extends ComponentPropsWithoutRef<"button"> {
  variant?: ButtonVariant;
}

export function Button({ variant = "secondary", className, ...rest }: ButtonProps) {
  return <button className={cx(BASE_CLASS, VARIANT_CLASSES[variant], className)} {...rest} />;
}
