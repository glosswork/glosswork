import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { getObjectType, type ObjectTypeDetail } from "../api/objectTypes";

/** One object type's full `describe_object_type` document, keyed by its `key`.
 * `includeSamples` drives `include_samples=true` (the schema editor's read path,
 * which reuses this same document rather than a second schema-shape parser). */
export function useObjectType(
  key: string | undefined,
  includeSamples: boolean = false,
): UseQueryResult<ObjectTypeDetail> {
  return useQuery({
    queryKey: ["object-types", key, { includeSamples }],
    queryFn: () => getObjectType(key as string, { include_samples: includeSamples }),
    enabled: key !== undefined,
  });
}
