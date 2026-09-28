/**
 * The per-object-type permissions panel (FR-U11, DD-11). A grant
 * is per object type, so the screen belongs on the type: `/schema/:objectTypeKey` is already the
 * per-type administrative surface and its other controls already require `admin` on the type.
 *
 * **Rendered for whoever holds `admin` on the type**, which is the one authority the three grant
 * routes check. It needs no system-role gate, because the two reads it needs both exist: the
 * picker offers people from `GET /principals/directory` (readable by any authenticated
 * principal), and an existing row is named from the `principals` sidecar on the grants document
 * itself.
 *
 * **The two reads are deliberately different, and one read cannot be both.** A picker offers
 * people you might grant to: active only, capped, and never offering a deactivated principal,
 * which is correct rather than a gap. A row label names whoever the row already names, live or
 * not, past any cap — so it comes from a map built from the rows' own ids, which is exact by
 * construction.
 *
 * A `member` cannot reach this panel and needs no clause to keep it out: `your_access` is
 * `min(credential scope, granted level)` and a session's scope is `roleScope(role)`, which maps
 * `member -> write`, so `admin` on the type is unreachable however generous the grant (DD-11).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  deleteObjectTypeGrant,
  listObjectTypeGrants,
  putObjectTypeGrant,
} from "../api/grants";
import { updateObjectType } from "../api/schemaAdmin";
import { LEVELS, type Level } from "../api/objectTypes";
import { objectTypesQueryKey } from "../hooks/useObjectTypes";
import { usePrincipalDirectory } from "../hooks/usePrincipalDirectory";
import { PrincipalName } from "../principals/PrincipalName";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { Select } from "../ui/Select";
import { Spinner } from "../ui/Spinner";
import { btnSmClass, fieldLabelClass } from "../ui/classes";
import { rowClass, tableClass, tableWrapClass, tdClass, thClass } from "../ui/tableClasses";

/** "No access (closed)" rather than "none": the word a person reads should say what the state
 * *is*, and every object type starts here. */
const LEVEL_LABELS: Record<Level, string> = {
  none: "No access (closed)",
  read: "read",
  write: "write",
  admin: "admin",
};

/** A grant row at `none` is an explicit **deny** that overrides a permissive default, which is
 * a different fact from "closed by default" and reads differently in a list of people. */
const GRANT_LEVEL_LABELS: Record<Level, string> = { ...LEVEL_LABELS, none: "Denied" };

export interface PermissionsPanelProps {
  objectTypeKey: string;
}

export function PermissionsPanel({ objectTypeKey }: PermissionsPanelProps) {
  const queryClient = useQueryClient();
  const grantsQueryKey = ["object-type-grants", objectTypeKey] as const;

  const grantsQuery = useQuery({
    queryKey: grantsQueryKey,
    queryFn: () => listObjectTypeGrants(objectTypeKey),
  });
  // The picker's source. The same argument-free hook every `user_ref` picker in the app
  // shares, so it dedupes onto one request; granting access to someone who has never touched
  // this type is the case this panel exists for, so resolving names from the DD-25 audit and
  // comment feeds — which carry a display name but only for people who have acted — would fail
  // on exactly that case. Row labels do not come from here: see the file docstring.
  const directoryQuery = usePrincipalDirectory();

  const [pickedPrincipal, setPickedPrincipal] = useState("");
  const [pickedLevel, setPickedLevel] = useState<Level>("read");

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: grantsQueryKey });
    // A grant change moves `your_access` on the orientation document, which is what every
    // affordance gate on every screen reads. Refetch it or the nav and the gates stay stale.
    void queryClient.invalidateQueries({ queryKey: objectTypesQueryKey });
  };

  const setDefaultLevel = useMutation({
    // `update_object_type` already accepts and validates `default_level`
    // (`services/schema.py:291, 301-304`) and it persists because the column is in
    // `_TYPE_COLUMNS`. No backend change is needed for either half of this panel.
    mutationFn: (level: Level) => updateObjectType(objectTypeKey, { default_level: level }),
    onSuccess: refresh,
  });

  const setGrant = useMutation({
    mutationFn: ({ principalId, level }: { principalId: string; level: Level }) =>
      putObjectTypeGrant(objectTypeKey, principalId, level),
    onSuccess: refresh,
  });

  const removeGrant = useMutation({
    mutationFn: (principalId: string) => deleteObjectTypeGrant(objectTypeKey, principalId),
    onSuccess: refresh,
  });

  const grants = grantsQuery.data?.grants ?? [];
  const principals = grantsQuery.data?.principals;
  const grantedIds = new Set(grants.map((grant) => grant.principal_id));
  const ungranted = (directoryQuery.data ?? []).filter(
    (principal) => !grantedIds.has(principal.id),
  );

  return (
    <section aria-label="Permissions" className="space-y-4">
      <h2 className="text-lg font-semibold text-ink">Permissions</h2>

      {grantsQuery.isLoading && <Spinner label="Loading permissions..." />}
      {grantsQuery.isError && <Alert tone="error" title="Could not load permissions." />}
      {setDefaultLevel.isError && (
        <Alert
          tone="error"
          title="Could not change the default access."
          error={setDefaultLevel.error}
        />
      )}
      {setGrant.isError && (
        <Alert tone="error" title="Could not save the grant." error={setGrant.error} />
      )}
      {removeGrant.isError && (
        <Alert tone="error" title="Could not remove the grant." error={removeGrant.error} />
      )}

      {grantsQuery.data && (
        <>
          <label className="flex max-w-xs flex-col gap-1">
            <span className={fieldLabelClass}>Default access</span>
            <Select
              value={grantsQuery.data.default_level}
              onChange={(event) => setDefaultLevel.mutate(event.target.value as Level)}
            >
              {LEVELS.map((level) => (
                <option key={level} value={level}>
                  {LEVEL_LABELS[level]}
                </option>
              ))}
            </Select>
          </label>
          <p className="max-w-xl text-xs text-ink-2">
            What a principal with no row below holds on this object type.
          </p>

          <div className="space-y-2">
            <h3 className="text-sm font-semibold text-ink">Who has explicit access</h3>
            {grants.length === 0 ? (
              <EmptyState title="No explicit grants." />
            ) : (
              <div className={tableWrapClass}>
                <table className={tableClass}>
                  <thead>
                    <tr>
                      <th className={thClass}>Principal</th>
                      <th className={thClass}>Level</th>
                      <th className={thClass} />
                    </tr>
                  </thead>
                  <tbody>
                    {grants.map((grant) => {
                      const principal = principals?.[grant.principal_id];
                      return (
                        <tr
                          key={grant.principal_id}
                          data-testid={`grant-${grant.principal_id}`}
                          className={rowClass}
                        >
                          <td className={tdClass}>
                            <PrincipalName id={grant.principal_id} principals={principals} />
                          </td>
                          <td className={tdClass}>
                            <Select
                              className="max-w-[12rem]"
                              aria-label={`Level for ${principal?.display_name ?? grant.principal_id}`}
                              value={grant.level}
                              onChange={(event) =>
                                setGrant.mutate({
                                  principalId: grant.principal_id,
                                  level: event.target.value as Level,
                                })
                              }
                            >
                              {LEVELS.map((level) => (
                                <option key={level} value={level}>
                                  {GRANT_LEVEL_LABELS[level]}
                                </option>
                              ))}
                            </Select>
                          </td>
                          <td className={tdClass}>
                            <Button
                              type="button"
                              variant="danger"
                              className={btnSmClass}
                              onClick={() => removeGrant.mutate(grant.principal_id)}
                            >
                              Remove
                            </Button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1">
              <span className={fieldLabelClass}>Grant access to</span>
              <Select
                className="max-w-[14rem]"
                aria-label="Grant access to"
                value={pickedPrincipal}
                onChange={(event) => setPickedPrincipal(event.target.value)}
              >
                <option value="">Choose someone...</option>
                {ungranted.map((principal) => (
                  <option key={principal.id} value={principal.id}>
                    {principal.display_name}
                  </option>
                ))}
              </Select>
            </label>
            <label className="flex flex-col gap-1">
              <span className={fieldLabelClass}>Level</span>
              <Select
                className="max-w-[12rem]"
                aria-label="Level to grant"
                value={pickedLevel}
                onChange={(event) => setPickedLevel(event.target.value as Level)}
              >
                {LEVELS.map((level) => (
                  <option key={level} value={level}>
                    {GRANT_LEVEL_LABELS[level]}
                  </option>
                ))}
              </Select>
            </label>
            <Button
              type="button"
              variant="primary"
              className={btnSmClass}
              disabled={pickedPrincipal === ""}
              onClick={() => {
                setGrant.mutate({ principalId: pickedPrincipal, level: pickedLevel });
                setPickedPrincipal("");
              }}
            >
              Grant
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
