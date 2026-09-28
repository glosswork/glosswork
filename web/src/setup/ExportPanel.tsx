/**
 * The Export card on `/setup` (FR-E5, FR-U9; DD-36; docs/DESIGN.md 8.6).
 *
 * **An `<a download>`, not a button, and that is the whole design.** `GET /api/v1/admin/export`
 * is a safe method (`routes/admin_ops.py`), so the browser sends the session cookie, follows the
 * route's own `Content-Disposition`, and streams the response straight to disk. Nothing is held
 * in memory and no JavaScript runs. `ui/Button.tsx` renders a `<button>` and has no `as` escape
 * hatch, so the established recipe for this is `linkButtonClass`, which exists for exactly it.
 *
 * **Backup is deliberately absent, and this is the only place that says so in the product.**
 * `POST /api/v1/admin/backup` is live and produces a tar of a consistent database snapshot plus
 * the blob tree (FR-P8). It cannot be a link, because it is a write: `middleware.py` requires a
 * session-authenticated request outside `SAFE_METHODS` to echo `X-GW-CSRF`, a header no HTML
 * form can set. Reaching it from the browser therefore means `fetch()` and a Blob, with the
 * entire artifact resident in browser memory, on the one endpoint whose output is unbounded by
 * construction. DD-36 already reached the same conclusion for its neighbour: restore is an
 * operator procedure because it would overwrite the database it is served from. Both live in
 * docs/DEPLOYMENT.md, which is where an operator is standing when they need either.
 *
 * Admin-only: `SetupPage` owns the gate, as it does for the search index.
 */
import { Card, cardHeadingClass } from "../ui/Card";
import { linkButtonClass } from "../ui/classes";

/** The route is absolute rather than composed, for the reason docs/DESIGN.md 8.5 gives about the
 * MCP URL: a URL assembled in the browser is a URL about the browser. This one is same-origin by
 * definition, because the credential that authorizes it is this document's own cookie. */
const EXPORT_PATH = "/api/v1/admin/export";

export function ExportPanel() {
  return (
    <Card label="Export" heading={<h2 className={cardHeadingClass}>Export</h2>}>
      <div className="space-y-3 p-3.5">
        <p className="max-w-prose text-sm text-ink-2">
          One JSON document holding this deployment's schema, records, links, comments, saved
          views, agent labels and audit trail (FR-E5). It is a portable copy for another store,
          not a restore point.
        </p>
        <a
          href={EXPORT_PATH}
          download
          data-testid="export-link"
          className={linkButtonClass}
        >
          Download export
        </a>
        <p className="max-w-prose text-xs text-ink-2">
          A backup of the whole deployment, database snapshot and attachment blobs together, is an
          operator procedure rather than a download: see the backup and restore section of
          docs/DEPLOYMENT.md.
        </p>
      </div>
    </Card>
  );
}
