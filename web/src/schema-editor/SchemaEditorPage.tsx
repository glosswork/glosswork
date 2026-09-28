import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { levelAllows, type FieldDoc, type ObjectTypeDetail } from "../api/objectTypes";
import { useAuth } from "../auth/useAuth";
import { PermissionsPanel } from "./PermissionsPanel";
import { ReadOnlyBanner } from "../access/ReadOnlyBanner";
import {
  createObjectType,
  proposeSchemaChange,
  updateObjectType,
  type FieldUpdatePending,
  type ProposalCreatedDoc,
} from "../api/schemaAdmin";
import { useObjectType } from "../hooks/useObjectType";
import { parseApiError } from "../table-view/apiErrors";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { Spinner } from "../ui/Spinner";
import { fieldErrorClass, fieldLabelClass, inputClass, selectClass } from "../ui/classes";
import { thClass, tdClass, tableClass, tableWrapClass, rowClass } from "../ui/tableClasses";
import { BlastRadiusPanel } from "./BlastRadiusPanel";
import { AgentFacingLabel, guidancePanelClass } from "./descriptionPanel";
import { DESCRIPTION_EXAMPLES, DESCRIPTION_GUIDANCE, validateDescription } from "./descriptionGuidance";
import { FieldEditor } from "./FieldEditor";

type PendingInfo = FieldUpdatePending | ProposalCreatedDoc;
type FieldPanel = { mode: "add" } | { mode: "edit"; field: FieldDoc } | null;

/** FR-U4: create-or-edit shell for one object type. Additive changes (create type,
 * add field, edit name/description, relax a constraint) apply immediately and this
 * screen reflects the applied state from the response with no separate confirmation
 * step (FR-S5). Destructive changes (delete field, delete object type, change field
 * type, remove an in-use enum option, tighten a violated constraint) never apply
 * here — they hand back a pending proposal, rendered by `BlastRadiusPanel`, which
 * links to Settings for the actual approve/reject decision. */
export function SchemaEditorPage() {
  const { objectTypeKey } = useParams<{ objectTypeKey: string }>();
  const isCreate = objectTypeKey === undefined;

  return isCreate ? (
    <CreateObjectType />
  ) : (
    // Keyed by objectTypeKey so navigating between two object types' editors fully
    // remounts (and resets local draft/guard state) rather than reusing the instance.
    <EditObjectType key={objectTypeKey} objectTypeKey={objectTypeKey} />
  );
}

function CreateObjectType() {
  const navigate = useNavigate();
  const { principal } = useAuth();
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [namePlural, setNamePlural] = useState("");
  const [description, setDescription] = useState("");
  const [keyPrefix, setKeyPrefix] = useState("");
  const [descriptionError, setDescriptionError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const descError = validateDescription(description, "Object type");
    setDescriptionError(descError);
    if (descError) return;

    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const created = await createObjectType({
        key: key.trim(),
        name,
        name_plural: namePlural,
        description,
        key_prefix: keyPrefix.trim(),
      });
      navigate(`/schema/${created.key}`);
    } catch (caught) {
      const envelope = parseApiError(caught);
      setSubmitError(envelope?.message ?? "The request failed.");
    } finally {
      setIsSubmitting(false);
    }
  };

  // Hiding the "Create object type" link on the schema index gates the *link*, not the route:
  // a bookmark or a typed URL still lands here, exactly as it does on the CSV import wizard.
  // `create_object_type` requires `role >= creator`, so a `member` reaching this form would fill
  // it in and earn a 403 on submit.
  if (principal?.role !== "admin" && principal?.role !== "creator") {
    return (
      <section aria-label="Create object type" className="max-w-2xl space-y-4">
        <h1 className="text-2xl font-semibold text-ink">Create object type</h1>
        <Alert
          tone="info"
          title="Object types are created by administrators. Ask one if you need a new type."
        />
      </section>
    );
  }

  return (
    <section aria-label="Create object type" className="max-w-2xl space-y-4">
      <h1 className="text-2xl font-semibold text-ink">Create object type</h1>
      <form onSubmit={(event) => void handleSubmit(event)} className="space-y-3">
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>Key</span>
          <input className={inputClass} value={key} onChange={(event) => setKey(event.target.value)} />
        </label>
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>Name</span>
          <input className={inputClass} value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>Plural name</span>
          <input
            className={inputClass}
            value={namePlural}
            onChange={(event) => setNamePlural(event.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>Key prefix</span>
          <input
            className={inputClass}
            value={keyPrefix}
            onChange={(event) => setKeyPrefix(event.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <AgentFacingLabel>Description</AgentFacingLabel>
          <textarea
            aria-label="Description"
            className={inputClass}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <p className={guidancePanelClass}>
          {DESCRIPTION_GUIDANCE} {DESCRIPTION_EXAMPLES.object_type}
        </p>
        {descriptionError && (
          <p role="alert" className={fieldErrorClass}>
            {descriptionError}
          </p>
        )}
        {submitError && (
          <p role="alert" className={fieldErrorClass}>
            {submitError}
          </p>
        )}
        <Button type="submit" variant="primary" disabled={isSubmitting}>
          Create
        </Button>
      </form>
      <p className="text-sm text-ink-2">Fields can be added once the object type is created.</p>
    </section>
  );
}

function EditObjectType({ objectTypeKey }: { objectTypeKey: string }) {
  const objectTypeQuery = useObjectType(objectTypeKey, true);
  const [objectType, setObjectType] = useState<ObjectTypeDetail | undefined>(undefined);
  const [name, setName] = useState("");
  const [namePlural, setNamePlural] = useState("");
  const [description, setDescription] = useState("");
  /** The **stored** `display_field_key`, not the effective one. A type that has
   * never chosen shows "(first field)" selected, so saving an unrelated setting on this form
   * cannot silently convert an implicit null into an explicit pin the user never made. `""`
   * is the null option's value, since a `<select>` option value is always a string. */
  const [displayFieldKey, setDisplayFieldKey] = useState("");
  const [descriptionError, setDescriptionError] = useState<string | null>(null);
  const [headerError, setHeaderError] = useState<string | null>(null);
  const [fieldPanel, setFieldPanel] = useState<FieldPanel>(null);
  const [pending, setPending] = useState<PendingInfo | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // Guards the one-time sync below so it fires exactly once per loaded object type,
  // not on every render (mirrors TableView.tsx's `appliedDefaultRef` pattern for
  // seeding local draft state from a query result the first time it arrives).
  const loadedRef = useRef(false);

  useEffect(() => {
    if (loadedRef.current || !objectTypeQuery.data) return;
    loadedRef.current = true;
    const loaded = objectTypeQuery.data;
    setObjectType(loaded);
    setName(loaded.name);
    setNamePlural(loaded.name_plural);
    setDescription(loaded.description);
    setDisplayFieldKey(loaded.display_field_key ?? "");
  }, [objectTypeQuery.data]);

  if (objectTypeQuery.isLoading) return <Spinner />;
  if (objectTypeQuery.isError || !objectType) return <EmptyState title="Object type not found." />;

  /**
   * The one screen in the app with two thresholds, and the row most likely to be
   * gated wrongly. Delete field and Delete object type only *propose* — both go through
   * `proposeSchemaChange` -> `propose_schema_change`, `require_level("write")` at
   * `services/schema.py:439`, and proposing is at `write` precisely because proposing is not
   * deciding. They look like the most privileged controls here and they are the least.
   * `add_field` (`:345`), `update_field` (`:371`) and `update_object_type` (`:308`) are the
   * `admin` ones. Gating the Deletes at `admin` would hide from a `write` caller a control the
   * server accepts, a failure in the direction hardest to notice.
   */
  const canPropose = levelAllows(objectType.your_access, "write");
  const canAdmin = levelAllows(objectType.your_access, "admin");

  /**
   * DD-11. One threshold, and it is the same one the three grant routes check: `admin` **on this
   * object type**, never the system role. No `principal.role === "admin"` clause is conjoined
   * onto it: the panel needs no principal field only `GET /api/v1/principals` carries, because
   * the principal directory and the `principals` sidecar on the grants document supply the
   * picker and the row labels respectively. So this reaches a `creator` administering the type
   * it defined and nobody else — a `member`'s session scope caps `your_access` at `write`
   * (DD-11), so `canAdmin` is unreachable for one however generous its grant.
   */
  const canManagePermissions = canAdmin;

  const handleHeaderSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const descError = validateDescription(description, "Object type");
    setDescriptionError(descError);
    if (descError) return;
    setHeaderError(null);
    try {
      const changes: Record<string, unknown> = {};
      if (name !== objectType.name) changes.name = name;
      if (namePlural !== objectType.name_plural) changes.name_plural = namePlural;
      if (description !== objectType.description) changes.description = description;
      // `null` rather than `""` clears the choice: an explicit null is the way back to the
      // derived rule, and `""` is not a field key the server would accept.
      if (displayFieldKey !== (objectType.display_field_key ?? "")) {
        changes.display_field_key = displayFieldKey === "" ? null : displayFieldKey;
      }
      if (Object.keys(changes).length === 0) return;
      const updated = await updateObjectType(objectTypeKey, changes);
      setObjectType(updated);
      setDisplayFieldKey(updated.display_field_key ?? "");
    } catch (caught) {
      const envelope = parseApiError(caught);
      setHeaderError(envelope?.message ?? "The request failed.");
    }
  };

  const handleDeleteObjectType = async () => {
    setActionError(null);
    try {
      const result = await proposeSchemaChange({
        change_type: "delete_object_type",
        object_type: objectTypeKey,
      });
      setFieldPanel(null);
      setPending(result);
    } catch (caught) {
      const envelope = parseApiError(caught);
      setActionError(envelope?.message ?? "The request failed.");
    }
  };

  const handleDeleteField = async (field: FieldDoc) => {
    setActionError(null);
    try {
      const result = await proposeSchemaChange({
        change_type: "delete_field",
        object_type: objectTypeKey,
        field_key: field.key,
      });
      setFieldPanel(null);
      setPending(result);
    } catch (caught) {
      const envelope = parseApiError(caught);
      setActionError(envelope?.message ?? "The request failed.");
    }
  };

  const handleFieldApplied = (field: FieldDoc) => {
    setObjectType((prev) => {
      if (!prev) return prev;
      const exists = prev.fields.some((candidate) => candidate.key === field.key);
      const fields = exists
        ? prev.fields.map((candidate) => (candidate.key === field.key ? field : candidate))
        : [...prev.fields, field];
      return { ...prev, fields, field_count: fields.length };
    });
    setFieldPanel(null);
  };

  const handleFieldPending = (result: FieldUpdatePending) => {
    setFieldPanel(null);
    setPending(result);
  };

  return (
    <section aria-label="Object type schema" className="space-y-6">
      <h1 className="text-2xl font-semibold text-ink">{objectType.name}</h1>

      <ReadOnlyBanner
        typeName={objectType.name}
        level={objectType.your_access}
        required="admin"
      />

      {pending ? (
        <BlastRadiusPanel
          proposalId={pending.proposal_id}
          changeType={pending.change_type}
          impact={pending.impact}
          message={pending.message}
          onAcknowledge={() => setPending(null)}
        />
      ) : (
        <>
          {canAdmin && (
          <form onSubmit={(event) => void handleHeaderSubmit(event)} className="max-w-2xl space-y-3">
            <label className="flex flex-col gap-1">
              <span className={fieldLabelClass}>Key</span>
              <input className={inputClass} value={objectType.key} disabled />
            </label>
            <label className="flex flex-col gap-1">
              <span className={fieldLabelClass}>Key prefix</span>
              <input className={inputClass} value={objectType.key_prefix} disabled />
            </label>
            <label className="flex flex-col gap-1">
              <span className={fieldLabelClass}>Name</span>
              <input className={inputClass} value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            <label className="flex flex-col gap-1">
              <span className={fieldLabelClass}>Plural name</span>
              <input
                className={inputClass}
                value={namePlural}
                onChange={(event) => setNamePlural(event.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1">
              <AgentFacingLabel>Description</AgentFacingLabel>
              <textarea
                aria-label="Description"
                className={inputClass}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </label>
            <p className={guidancePanelClass}>
              {DESCRIPTION_GUIDANCE} {DESCRIPTION_EXAMPLES.object_type}
            </p>
            {/* No gate of its own: this form is already wrapped in
             * `{canAdmin && ...}` above, so the select inherits `levelAllows(yourAccess,
             * "admin")` along with every other control on it, which is the
             * level `update_object_type` requires. Options are filtered on the wire's
             * `display_eligible` flag -- the one implementation of the rule lives in
             * `fieldtypes.py::is_display_eligible` and is never restated here. */}
            <label className="flex flex-col gap-1">
              <span className={fieldLabelClass}>Display field</span>
              <select
                aria-label="Display field"
                className={selectClass}
                value={displayFieldKey}
                onChange={(event) => setDisplayFieldKey(event.target.value)}
              >
                <option value="">(first field)</option>
                {objectType.fields
                  .filter((candidate) => candidate.display_eligible)
                  .map((candidate) => (
                    <option key={candidate.key} value={candidate.key}>
                      {candidate.name}
                    </option>
                  ))}
              </select>
              <span className="text-xs text-ink-2">
                Which field&rsquo;s value names a record: in search results, beside every link
                to it, and in an agent&rsquo;s compact reply. &ldquo;(first field)&rdquo; leaves
                it derived from field order.
              </span>
            </label>
            {descriptionError && (
              <p role="alert" className={fieldErrorClass}>
                {descriptionError}
              </p>
            )}
            {headerError && (
              <p role="alert" className={fieldErrorClass}>
                {headerError}
              </p>
            )}
            <Button type="submit" variant="primary">
              Save
            </Button>
          </form>
          )}

          {canPropose && (
            <div className="space-y-1.5">
              <Button type="button" variant="danger" onClick={() => void handleDeleteObjectType()}>
                Delete object type
              </Button>
              {actionError && (
                <p role="alert" className={fieldErrorClass}>
                  {actionError}
                </p>
              )}
            </div>
          )}

          <div className="space-y-2">
            <h2 className="text-lg font-semibold text-ink">Fields</h2>
            <div className={tableWrapClass}>
              <table className={tableClass}>
                <thead>
                  <tr>
                    <th className={thClass}>Key</th>
                    <th className={thClass}>Name</th>
                    <th className={thClass}>Type</th>
                    <th className={thClass}>Description</th>
                    <th className={thClass} />
                  </tr>
                </thead>
                <tbody>
                  {objectType.fields.map((field) => (
                    <tr key={field.key} data-testid={`field-row-${field.key}`} className={rowClass}>
                      <td className={`${tdClass} font-mono text-xs text-ink-2`}>{field.key}</td>
                      <td className={tdClass}>{field.name}</td>
                      <td className={tdClass}>{field.type}</td>
                      <td className={`${tdClass} whitespace-normal text-ink-2`}>{field.description}</td>
                      <td className={tdClass}>
                        <div className="flex flex-wrap gap-1.5">
                          {canAdmin && (
                            <Button
                              type="button"
                              variant="secondary"
                              className="px-2.5 py-1 text-sm"
                              onClick={() => setFieldPanel({ mode: "edit", field })}
                            >
                              Edit
                            </Button>
                          )}
                          {canPropose && (
                            <Button
                              type="button"
                              variant="danger"
                              className="px-2.5 py-1 text-sm"
                              onClick={() => void handleDeleteField(field)}
                            >
                              Delete
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {canAdmin && fieldPanel === null && (
            <Button type="button" variant="secondary" onClick={() => setFieldPanel({ mode: "add" })}>
              Add field
            </Button>
          )}

          {fieldPanel && (
            <FieldEditor
              objectTypeKey={objectTypeKey}
              field={fieldPanel.mode === "edit" ? fieldPanel.field : undefined}
              onApplied={handleFieldApplied}
              onPending={handleFieldPending}
              onCancel={() => setFieldPanel(null)}
            />
          )}

          {/* Last on the screen, deliberately: placed above the field editor it shifted that
              form's y-offset by a fraction of a pixel and repainted `schema-field-editor.png`
              for no reason anyone reviewing the diff could have named. It is also the right
              place semantically — who may reach this type is a separate concern from what its
              fields are. */}
          {canManagePermissions && <PermissionsPanel objectTypeKey={objectTypeKey} />}
        </>
      )}
    </section>
  );
}
