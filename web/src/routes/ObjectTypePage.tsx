import { useParams } from "react-router-dom";
import { useObjectType } from "../hooks/useObjectType";
import { ObjectTypeTablePlaceholder } from "./ObjectTypeTablePlaceholder";
import { EmptyState } from "../ui/EmptyState";
import { Spinner } from "../ui/Spinner";

/** Route component for `/:objectTypeKey`: fetches the object type and hands off to the shell. */
export function ObjectTypePage() {
  const { objectTypeKey } = useParams<{ objectTypeKey: string }>();
  const { data: objectType, isLoading, isError } = useObjectType(objectTypeKey);

  if (isLoading) {
    return <Spinner />;
  }

  if (isError || !objectType) {
    return <EmptyState title="Object type not found." />;
  }

  return <ObjectTypeTablePlaceholder objectType={objectType} />;
}
