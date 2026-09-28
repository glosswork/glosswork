import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { listObjectTypes, type ObjectTypeSummary } from "../api/objectTypes";

export const objectTypesQueryKey = ["object-types"] as const;

/** Every live object type (`list_object_types`), used to drive the app shell's nav. */
export function useObjectTypes(): UseQueryResult<ObjectTypeSummary[]> {
  return useQuery({
    queryKey: objectTypesQueryKey,
    queryFn: listObjectTypes,
  });
}
