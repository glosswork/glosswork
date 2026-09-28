/**
 * Form-field composition. Emits label, control, help, and error as SIBLINGS — deliberately no
 * wrapper element: it adds no new container, because the screens that adopt this already own
 * whatever wrapper they have. The label association is `htmlFor`/`id`, exactly what
 * `getByLabelText` queries.
 *
 * Screens whose markup doesn't match this shape apply the recipes in `classes.ts` to their
 * existing elements instead.
 */
import type { ReactNode } from "react";
import { fieldErrorClass, fieldHelpClass, fieldLabelClass } from "./classes";

export interface FieldProps {
  /** The control's `id`; the caller sets the same id on the child control. */
  id: string;
  label: ReactNode;
  help?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
}

export function Field({ id, label, help, error, children }: FieldProps) {
  return (
    <>
      <label htmlFor={id} className={fieldLabelClass}>
        {label}
      </label>
      {children}
      {help && <p className={fieldHelpClass}>{help}</p>}
      {error && (
        <p role="alert" className={fieldErrorClass}>
          {error}
        </p>
      )}
    </>
  );
}
