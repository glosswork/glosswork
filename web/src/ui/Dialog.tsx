/**
 * Modal dialog on the native `<dialog>` element (DD-41): `showModal()` gives
 * focus containment, Escape, `aria-modal`, the top layer, and focus return on close from
 * the browser engine — correct by construction, no dependency, no portal. The implicit
 * role is `dialog`, so `getByRole("dialog", { name })` queries keep working.
 *
 * Mount/unmount is the open/close model, matching how both call sites already render
 * (`{conflict && <MergeConflictDialog … />}`): mounting calls `showModal()`, unmounting
 * closes. Escape fires the native `cancel` event; it is prevented and reported through
 * `onCancel` so the parent unmounts — state stays in React.
 *
 * jsdom 30.0.1 does not implement `showModal`/`close`; `test/setup.ts` carries the shim.
 */
import { useEffect, useRef } from "react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { cx } from "./cx";

export interface DialogProps extends Omit<ComponentPropsWithoutRef<"dialog">, "onCancel"> {
  /** Accessible name; rendered as `aria-label`. */
  label: string;
  /** Called on Escape (the native `cancel` event). The parent unmounts in response. */
  onCancel?: () => void;
  children: ReactNode;
}

export function Dialog({ label, onCancel, className, children, ...rest }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) {
      dialog.showModal();
    }
    return () => {
      if (dialog?.open) {
        dialog.close();
      }
    };
  }, []);

  return (
    <dialog
      ref={ref}
      aria-label={label}
      onCancel={(event) => {
        event.preventDefault();
        onCancel?.();
      }}
      className={cx(
        "m-auto w-full max-w-xl rounded-card border border-line bg-surface p-5 text-ink",
        "shadow-xl backdrop:bg-ink/30",
        className,
      )}
      {...rest}
    >
      {children}
    </dialog>
  );
}
