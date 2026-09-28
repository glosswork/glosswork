/**
 * `/setup` (docs/DESIGN.md 8.6): how this deployment is configured, as against `/people`,
 * which is who is on it.
 *
 * **Replaces the second half of `/settings`.** The split is by question rather than by
 * permission, which is why the personal access tokens card is here and visible to everyone
 * while the two cards beside it are admin-only: a token is a fact about your access to this
 * deployment, not about who else is on it.
 *
 * The two admin cards are not rendered at all for anyone else, rather than rendered and left to
 * a `403` -- `GET /api/v1/admin/search-index` and `GET /api/v1/admin/export` both declare the
 * `admin` role (FR-I10).
 *
 * **`PasswordPanel` renders for every `type === "user"` principal**, right
 * after `AccessTokensPanel`: a service account has no browser session to change a password from,
 * and a `user` gets the card whatever their `auth_provider` -- the card itself is what turns an
 * OIDC principal into a sentence instead of a form.
 */
import { useAuth } from "../auth/useAuth";
import { AccessTokensPanel } from "./AccessTokensPanel";
import { ExportPanel } from "./ExportPanel";
import { PasswordPanel } from "./PasswordPanel";
import { SearchIndexPanel } from "./SearchIndexPanel";

export function SetupPage() {
  const { principal } = useAuth();
  const isAdmin = principal?.role === "admin";

  return (
    <div className="max-w-5xl space-y-4">
      <h1 className="font-display text-2xl font-semibold text-ink">Setup</h1>
      <AccessTokensPanel />
      {principal?.type === "user" && <PasswordPanel />}
      {isAdmin && <SearchIndexPanel />}
      {isAdmin && <ExportPanel />}
    </div>
  );
}
