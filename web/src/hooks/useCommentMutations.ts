import {
  useMutation,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";
import { addComment, deleteComment, updateComment, type CommentDoc } from "../api/comments";
import { commentsQueryKey } from "./useComments";

/** Adds a comment and refreshes the thread in place, no full page reload (FR-U2, FR-C2). */
export function useAddComment(ref: string): UseMutationResult<CommentDoc, unknown, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: string) => addComment(ref, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: commentsQueryKey(ref) });
    },
  });
}

export interface UpdateCommentVariables {
  commentId: string;
  body: string;
}

/** Edits one of the caller's own comments; the thread refetches to show the new body and the
 * `edited` marker (FR-C5, FR-C6). */
export function useUpdateComment(
  ref: string,
): UseMutationResult<CommentDoc, unknown, UpdateCommentVariables> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ commentId, body }: UpdateCommentVariables) => updateComment(commentId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: commentsQueryKey(ref) });
    },
  });
}

/** Soft-deletes a comment; the thread refetches and no longer lists it (FR-C5, FR-C6). */
export function useDeleteComment(ref: string): UseMutationResult<CommentDoc, unknown, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (commentId: string) => deleteComment(commentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: commentsQueryKey(ref) });
    },
  });
}
