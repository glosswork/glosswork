import { Navigate, useLocation, useParams } from "react-router-dom";
import { useObjectType } from "../hooks/useObjectType";
import { lookupHidesType, oldAddressTarget } from "./oldAddresses";
import { ObjectTypeTablePlaceholder } from "./ObjectTypeTablePlaceholder";
import { EmptyState } from "../ui/EmptyState";
import { Spinner } from "../ui/Spinner";

/**
 * Route component for `/:objectTypeKey`: fetches the object type and hands off to the shell.
 *
 * It is also where an old screen address resolves (`oldAddresses.ts`), only after the lookup has
 * answered, so a readable type with that key always keeps its table.
 *
 * `replace`, not a push. A pushed redirect leaves the old address on the history stack, so Back
 * lands on it, redirects forward again, and the button stops working.
 */
export function ObjectTypePage() {
  const { objectTypeKey } = useParams<{ objectTypeKey: string }>();
  const { search, hash } = useLocation();
  const { data: objectType, isLoading, isError, error } = useObjectType(objectTypeKey);

  if (isLoading) {
    return <Spinner />;
  }

  if (isError && lookupHidesType(error)) {
    const target = oldAddressTarget(objectTypeKey ?? "", { search, hash });
    if (target) {
      return <Navigate to={target} replace />;
    }
  }

  if (isError || !objectType) {
    return <EmptyState title="Object type not found." />;
  }

  return <ObjectTypeTablePlaceholder objectType={objectType} />;
}
