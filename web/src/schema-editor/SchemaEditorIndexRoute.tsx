import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { useObjectTypes } from "../hooks/useObjectTypes";
import { Alert } from "../ui/Alert";
import { Spinner } from "../ui/Spinner";
import { linkButtonClass } from "../ui/classes";

/** FR-U4: the schema editor's landing screen — every live object type, plus the
 * entry point for creating a new one. */
export function SchemaEditorIndexRoute() {
  const { data: objectTypes, isLoading, isError } = useObjectTypes();
  const { principal } = useAuth();

  /**
   * The one affordance gated by a **role** rather than a level, and necessarily: no object type
   * exists yet to hold a level on, so `create_object_type`'s rule is `role >= creator`. Not the
   * `ReadOnlyBanner`, which needs an object type to name and governs level gates, and this is
   * not one — but the absence still gets a sentence, because a hidden control with no explanation
   * is the failure mode both are guarding against.
   */
  const canCreate = principal?.role === "admin" || principal?.role === "creator";

  // The list itself is already filtered to types this caller may read (FR-I11, arriving through
  // `list_object_types` with no frontend change), so nothing below needs a level check.

  return (
    <section aria-label="Schema" className="max-w-2xl space-y-3">
      <h1 className="text-2xl font-semibold text-ink">Schema</h1>
      <p className="text-base text-ink-2">
        Create and edit object types and fields with the same power as the admin API,
        including the description every field carries for agents to read.
      </p>
      {canCreate ? (
        <Link to="/schema/new" className={linkButtonClass}>
          Create object type
        </Link>
      ) : (
        <p className="text-sm text-ink-2">
          Object types are created by administrators. Ask one if you need a new type.
        </p>
      )}
      {isLoading && <Spinner label="Loading object types..." />}
      {isError && <Alert tone="error" title="Could not load object types." />}
      <ul className="divide-y divide-line rounded-card border border-line bg-surface">
        {(objectTypes ?? []).map((objectType) => (
          <li key={objectType.key} className="px-3.5 py-2 text-sm">
            <Link
              to={`/schema/${objectType.key}`}
              className="font-medium text-human-ink hover:underline"
            >
              {objectType.name}
            </Link>
            {" — "}
            <span className="text-ink-2">
              {objectType.field_count} field(s), {objectType.record_count} record(s)
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
