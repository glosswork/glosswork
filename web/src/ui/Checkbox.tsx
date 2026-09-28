/**
 * Labeled checkbox. The label wraps input + text — the shape
 * `FieldEditor.tsx` already uses — and the help line sits OUTSIDE the label on purpose:
 * text inside the label joins the control's accessible name, so help inside would change
 * every `getByLabelText` query that targets the checkbox.
 */
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { cx } from "./cx";

export interface CheckboxProps extends ComponentPropsWithoutRef<"input"> {
  children: ReactNode;
  help?: ReactNode;
}

export function Checkbox({ children, help, className, ...rest }: CheckboxProps) {
  return (
    <>
      <label className="inline-flex items-start gap-2 text-base text-ink">
        <input type="checkbox" className={cx("mt-0.5 accent-accent", className)} {...rest} />
        <span>{children}</span>
      </label>
      {help && <p className="mt-0.5 max-w-md pl-6 text-xs text-ink-2">{help}</p>}
    </>
  );
}
