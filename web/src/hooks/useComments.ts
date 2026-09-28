import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { listComments, type CommentPage } from "../api/comments";

export function commentsQueryKey(ref: string | undefined) {
  return ["comments", ref] as const;
}

/** A record's comment thread (`list_comments`), chronological, keyed by record ref. */
export function useComments(ref: string | undefined): UseQueryResult<CommentPage> {
  return useQuery({
    queryKey: commentsQueryKey(ref),
    queryFn: () => listComments(ref as string),
    enabled: ref !== undefined,
  });
}
