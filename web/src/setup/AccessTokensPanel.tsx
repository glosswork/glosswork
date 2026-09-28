/**
 * The Personal access tokens card on `/setup` (FR-U9, FR-I4, FR-I5; docs/DESIGN.md 7.7, 8.6).
 *
 * **A table, not a `rounded-card` row per token with a `<dl>` inside it.** Expiry, last use and
 * revocation are facts you compare across tokens, which is what columns are for.
 *
 * Visible to any signed-in principal, not admin-gated: a `member` manages their own tokens over
 * `POST`/`GET`/`DELETE /api/v1/access-tokens`, which already scope listing and minting to the
 * caller unless an admin passes `principal_id` explicitly (not offered by this UI).
 *
 * The scope selector only offers scopes the caller may actually mint, computed from the two
 * ceilings `AccessTokenService._check_ceilings` enforces: the caller's role maps to a scope
 * through `roleScope` (a `member` caps out at `write`; a `creator`, like an `admin`, reaches
 * `admin`), and no credential can mint above its own `scope`. This is a courtesy -- the server
 * enforces both regardless, independently of what this component renders.
 *
 * **The mint form is inline, and `MintedTokenDialog` is its one dialog.** The invite and
 * service-account forms are native dialogs on `/people`; this one is not, because submitting it
 * opens a dialog, and a dialog opened from inside a dialog is a focus-return problem with
 * nothing to gain. The plaintext is shown exactly once.
 */
import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/useAuth";
import {
  listAccessTokens,
  mintAccessToken,
  revokeAccessToken,
  type AccessTokenDoc,
  type MintedAccessTokenDoc,
} from "../api/accessTokens";
import { roleScope, type PrincipalRole, type Scope } from "../api/principals";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Card, cardHeadingClass } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { EmptyState } from "../ui/EmptyState";
import { Select } from "../ui/Select";
import { Spinner } from "../ui/Spinner";
import { endOfPickedDayUtc, formatTimestamp, todayUtc } from "../ui/datetime";
import { btnSmClass, fieldLabelClass, inputClass } from "../ui/classes";
import {
  cardTableWrapClass,
  keyCellClass,
  rowClass,
  tableClass,
  tdClass,
  thClass,
  timeCellClass,
} from "../ui/tableClasses";
import { accessTokensQueryKey } from "./accessTokensQueryKey";

const SCOPE_ORDER: Record<Scope, number> = { read: 0, write: 1, admin: 2 };
const ALL_SCOPES: Scope[] = ["read", "write", "admin"];

/**
 * The scopes this caller may mint for themselves: capped by role and by their own credential's
 * scope (nobody mints above what they hold), the two ceilings `_check_ceilings` enforces.
 *
 * The role half reads `roleScope` rather than branching on a role string. It used to be
 * `role === "admin" ? "admin" : "write"`, which was correct for two roles and wrong for three:
 * `role_scope` maps `creator -> admin`, so a creator may mint an `admin` PAT, and needs one to
 * reach the schema routes at all. Branching on the string meant the one role that arrived with
 * per-object-type access could not obtain from this panel the one credential it exists to use.
 */
function mintableScopes(role: PrincipalRole, callerScope: Scope): Scope[] {
  const roleCeiling = roleScope(role);
  const ceiling = SCOPE_ORDER[roleCeiling] <= SCOPE_ORDER[callerScope] ? roleCeiling : callerScope;
  return ALL_SCOPES.filter((scope) => SCOPE_ORDER[scope] <= SCOPE_ORDER[ceiling]);
}

/** Shows the plaintext exactly once. Nothing keeps a copy after `onDismiss` runs -- there is no
 * subsequent read that could re-fetch it, by construction of the API. */
function MintedTokenDialog({
  token,
  onDismiss,
}: {
  token: MintedAccessTokenDoc;
  onDismiss: () => void;
}) {
  const [copyError, setCopyError] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(token.token);
      setCopyError(false);
    } catch {
      setCopyError(true);
    }
  }

  return (
    <Dialog label="New access token" data-testid="minted-token-dialog" onCancel={onDismiss}>
      <h2 className="mb-1 text-lg font-semibold text-ink">New access token</h2>
      <p className="mb-3 max-w-md text-sm text-ink-2">
        You will not see this token again. Copy it now and store it somewhere safe.
      </p>
      <code
        data-testid="minted-token-plaintext"
        className="block max-w-md break-all rounded-ctl border border-line-2 bg-ground px-2.5 py-1.5 font-mono text-xs text-ink"
      >
        {token.token}
      </code>
      <div className="mt-3 flex items-center gap-2">
        <Button type="button" variant="secondary" onClick={() => void handleCopy()}>
          Copy to clipboard
        </Button>
        <Button type="button" variant="primary" onClick={onDismiss}>
          Done
        </Button>
      </div>
      {copyError && (
        <p role="alert" className="mt-2 text-xs text-bad">
          Could not copy automatically; copy it manually.
        </p>
      )}
    </Dialog>
  );
}

function AccessTokenRow({ token, onRevoke }: { token: AccessTokenDoc; onRevoke: () => void }) {
  const [confirmingRevoke, setConfirmingRevoke] = useState(false);
  const isRevoked = token.revoked_at !== null;

  return (
    <tr data-testid={`access-token-${token.id}`} className={rowClass}>
      <td className={tdClass}>{token.name}</td>
      <td className={`${tdClass} text-ink-2`} data-testid={`access-token-agent-${token.id}`}>
        {token.agent_label ?? "none"}
      </td>
      <td className={`${tdClass} ${keyCellClass}`}>{token.token_prefix}</td>
      <td className={`${tdClass} text-ink-2`}>{token.scope}</td>
      <td className={`${tdClass} ${timeCellClass} text-ink-2`}>
        {token.expires_at ? formatTimestamp(token.expires_at) : "never"}
      </td>
      <td className={`${tdClass} ${timeCellClass} text-ink-2`}>
        {token.last_used_at ? formatTimestamp(token.last_used_at) : "never"}
      </td>
      <td className={`${tdClass} text-ink-2`} data-testid={`access-token-status-${token.id}`}>
        {isRevoked ? "Revoked" : "Active"}
      </td>
      <td className={`${tdClass} text-right`}>
        {!isRevoked &&
          (confirmingRevoke ? (
            <span className="inline-flex items-center gap-2 text-xs text-ink-2">
              <span>Revoke {token.name}?</span>
              <Button
                type="button"
                variant="danger"
                className={btnSmClass}
                onClick={() => {
                  onRevoke();
                  setConfirmingRevoke(false);
                }}
              >
                Confirm revoke
              </Button>
              <Button
                type="button"
                variant="quiet"
                className={btnSmClass}
                onClick={() => setConfirmingRevoke(false)}
              >
                Cancel
              </Button>
            </span>
          ) : (
            <Button
              type="button"
              variant="danger"
              className={btnSmClass}
              onClick={() => setConfirmingRevoke(true)}
            >
              Revoke
            </Button>
          ))}
      </td>
    </tr>
  );
}

/** Visible to any signed-in principal: mint, list, and revoke the caller's own personal access
 * tokens. */
export function AccessTokensPanel() {
  const { principal } = useAuth();
  const queryClient = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: accessTokensQueryKey,
    queryFn: () => listAccessTokens(),
  });

  const offeredScopes = principal ? mintableScopes(principal.role, principal.scope) : [];

  const [name, setName] = useState("");
  const [scope, setScope] = useState<Scope>(offeredScopes[0] ?? "read");
  const [expiresAt, setExpiresAt] = useState("");
  const [agentLabel, setAgentLabel] = useState("");
  const [mintedToken, setMintedToken] = useState<MintedAccessTokenDoc | null>(null);

  const mint = useMutation({
    mutationFn: () =>
      mintAccessToken({
        name,
        scope,
        // Routing the picked date through `Date` would send the browser's own ISO rendering,
        // which always carries three fractional digits; `timeutil.parse_datetime` is strict
        // against them, so every expiry a person picked would come back `validation_failed`.
        // The expression lives in a utility rather than here (AGENTS.md non-negotiable 3), and
        // what it means is written down in docs/DATA_MODEL.md section 4.
        expires_at: expiresAt ? endOfPickedDayUtc(expiresAt) : undefined,
        agent_label: agentLabel.trim() ? agentLabel.trim() : undefined,
      }),
    onSuccess: (minted) => {
      setMintedToken(minted);
      setName("");
      setExpiresAt("");
      setAgentLabel("");
      void queryClient.invalidateQueries({ queryKey: accessTokensQueryKey });
    },
  });

  const revoke = useMutation({
    mutationFn: (id: string) => revokeAccessToken(id),
    onSuccess: (updated) => {
      queryClient.setQueryData<AccessTokenDoc[]>(accessTokensQueryKey, (old) =>
        old?.map((token) => (token.id === updated.id ? updated : token)),
      );
    },
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    mint.mutate();
  }

  return (
    <Card
      label="Personal access tokens"
      heading={<h2 className={cardHeadingClass}>Personal access tokens</h2>}
      hint="Yours alone"
    >
      <div className="space-y-3 p-3.5">
        {isLoading && <Spinner label="Loading access tokens..." />}
        {isError && <Alert tone="error" title="Could not load access tokens." />}
        {revoke.isError && (
          <Alert tone="error" title="Could not revoke the token." error={revoke.error} />
        )}
        {data && data.length === 0 && (
          <EmptyState title="No access tokens yet." bordered={false} />
        )}
        {data && data.length > 0 && (
          <div className={cardTableWrapClass}>
            <table className={tableClass}>
              <thead>
                <tr>
                  <th className={thClass}>Name</th>
                  <th className={thClass}>Agent</th>
                  <th className={thClass}>Prefix</th>
                  <th className={thClass}>Scope</th>
                  <th className={thClass}>Expires</th>
                  <th className={thClass}>Last used</th>
                  <th className={thClass}>Status</th>
                  <th className={thClass} aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {data.map((token) => (
                  <AccessTokenRow
                    key={token.id}
                    token={token}
                    onRevoke={() => revoke.mutate(token.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        <form
          aria-label="Mint access token"
          onSubmit={handleSubmit}
          className="max-w-md space-y-3 border-t border-line pt-3"
        >
          <h3 className="text-sm font-semibold text-ink">Mint a new token</h3>
          <label className="flex flex-col gap-1">
            <span className={fieldLabelClass}>Name</span>
            <input
              id="new-token-name"
              className={inputClass}
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={fieldLabelClass}>Scope</span>
            <Select
              className="max-w-[10rem]"
              value={scope}
              onChange={(event) => setScope(event.target.value as Scope)}
            >
              {offeredScopes.map((offered) => (
                <option key={offered} value={offered}>
                  {offered}
                </option>
              ))}
            </Select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={fieldLabelClass}>Agent label (optional)</span>
            <input
              id="new-token-agent-label"
              className={inputClass}
              value={agentLabel}
              onChange={(event) => setAgentLabel(event.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={fieldLabelClass}>Expires at (optional)</span>
            {/* `min` is the current UTC date, not the viewer's local today: the value sent is
                the end of the picked day UTC, so a viewer in New York at 21:00 whose local
                today is already tomorrow in UTC would otherwise be offered a date the server
                refuses as past. Without it the form asks for a value and then rejects it. */}
            <input
              id="new-token-expires-at"
              type="date"
              className={inputClass}
              min={todayUtc()}
              value={expiresAt}
              onChange={(event) => setExpiresAt(event.target.value)}
            />
          </label>
          <Button type="submit" variant="primary" disabled={offeredScopes.length === 0}>
            Mint token
          </Button>
        </form>
        {mint.isError && (
          <Alert tone="error" title="Could not mint the token." error={mint.error} />
        )}
      </div>

      {mintedToken && (
        <MintedTokenDialog token={mintedToken} onDismiss={() => setMintedToken(null)} />
      )}
    </Card>
  );
}
