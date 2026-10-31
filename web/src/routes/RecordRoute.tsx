import { Navigate, useLocation, useParams } from "react-router-dom";
import { oldSchemaEditorTarget } from "./oldAddresses";
import { RecordPage } from "./RecordPage";

/**
 * Route component for `/:objectTypeKey/:recordKey`. The check sits in a wrapper rather than in
 * `RecordPage` so an old `/schema/new` sends no `GET /records/new` on its way past: `RecordPage`
 * starts its lookups as soon as it renders.
 *
 * `replace`, not a push. A pushed redirect leaves the old address on the history stack, so Back
 * lands on it, redirects forward again, and the button stops working.
 */
export function RecordRoute() {
  const { objectTypeKey, recordKey } = useParams<{ objectTypeKey: string; recordKey: string }>();
  const { search, hash } = useLocation();
  const target = oldSchemaEditorTarget(objectTypeKey ?? "", recordKey ?? "", { search, hash });

  return target ? <Navigate to={target} replace /> : <RecordPage />;
}
