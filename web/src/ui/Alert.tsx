/**
 * Inline alert, and the error-envelope mechanism: pass the caught mutation error and
 * the server's FR-A4 envelope (`{code, message}`) reaches the screen, with the fixed
 * string only as the fallback for an unparseable body. Built once here so applying it
 * across the Settings panels is mechanical.
 *
 * `error` tone announces via `role="alert"` — the convention every mutation-error line in
 * the suite already queries; the other tones are polite `role="status"`.
 */
import type { ReactNode } from "react";
import { parseApiError } from "../table-view/apiErrors";
import { cx } from "./cx";

export type AlertTone = "error" | "success" | "warning" | "info";

const TONE_CLASSES: Record<AlertTone, string> = {
  error: "border-bad-line bg-bad-soft text-bad",
  success: "border-ok-line bg-ok-soft text-ok",
  warning: "border-warn-line bg-warn-soft text-warn",
  info: "border-line bg-sunk text-ink-2",
};

export interface AlertProps {
  tone: AlertTone;
  /** The lead line — for a mutation failure, the screen's fixed string. */
  title: ReactNode;
  /** The caught error; when its body parses as the FR-A4 envelope, code and message render. */
  error?: unknown;
  children?: ReactNode;
  className?: string;
  "data-testid"?: string;
}

export function Alert({ tone, title, error, children, className, ...rest }: AlertProps) {
  const envelope = error === undefined ? null : parseApiError(error);
  const isForbidden = envelope !== null && envelope.code === "forbidden";
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cx(
        "grid max-w-xl gap-0.5 rounded-card border px-3.5 py-2.5 text-base",
        TONE_CLASSES[tone],
        className,
      )}
      {...rest}
    >
      {/* A `forbidden` message is the lead line, rendered verbatim and with no
          `code —` prefix: the backend writes that copy to name the type, the level held, the level
          required, and who can fix it, and the screen's own fixed title ("Could not save the
          comment.") editorializes over an explanation that is already better than it. Every
          other code keeps the title-plus-envelope shape. */}
      <span className="font-semibold">{isForbidden ? envelope.message : title}</span>
      {envelope && !isForbidden && (
        <span className="text-xs opacity-90">
          <code className="font-mono">{envelope.code}</code> — {envelope.message}
        </span>
      )}
      {children && <span className="text-xs opacity-90">{children}</span>}
    </div>
  );
}
