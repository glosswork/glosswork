/**
 * The server's own sentence from a failed request (the FR-A4 envelope's `error.message`), or
 * `fallback` when the body is not that envelope. Kept out of the component files so each exports
 * only a component, as `react-refresh/only-export-components` requires.
 */
import { ApiError } from "../api/client";

export function serverMessage(err: unknown, fallback: string): string {
  if (!(err instanceof ApiError)) return fallback;
  try {
    const body = JSON.parse(err.body) as { error?: { message?: string } };
    return body.error?.message ?? fallback;
  } catch {
    return fallback;
  }
}
