/**
 * The self-change password write path (FR-I17):
 * `POST /api/v1/me/password`.
 *
 * Modelled on `table-view/useCreateRecord.ts`: a `useMutation` so `app/queryClient.ts`'s
 * `mutationCache.onError` still sees a `forbidden` refusal for free, and `parseValidationFailed`
 * stays the one reader of the `{message, fieldKey}` envelope. The refusal this surface actually
 * renders is `validation_failed` naming `current_password` — a wrong current password, the
 * server's own floor message, the "someone else changed it" race guard, or the session-only
 * refusal, which arrives as `insufficient_scope` and so falls to the generic branch
 * below and renders its own message verbatim (DD-42) rather than being paraphrased here.
 *
 * On success this also invalidates the access-tokens query (`AccessTokensPanel`'s
 * `accessTokensQueryKey`): a password change revokes every personal access token the principal
 * holds in the same request, and the card above would otherwise keep showing them "Active".
 */
import { useCallback, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { changeOwnPassword, type CurrentPrincipal } from "../api/auth";
import { parseValidationFailed } from "../table-view/apiErrors";
import { accessTokensQueryKey } from "./accessTokensQueryKey";

export interface ChangeOwnPasswordFailure {
  /** The server's own message, rendered verbatim. */
  message: string;
  /** `"current_password"` when the server named that field; null for any other refusal
   * (the session-only 403, a rate limit, a network failure). */
  fieldKey: string | null;
}

export interface UseChangeOwnPasswordResult {
  submit: (currentPassword: string, newPassword: string) => Promise<boolean>;
  failure: ChangeOwnPasswordFailure | null;
  /** True once a submitted change has actually gone through; reset on the next `submit` call so
   * a second attempt does not show a stale success message while it is still pending. */
  succeeded: boolean;
  isPending: boolean;
}

export function useChangeOwnPassword(): UseChangeOwnPasswordResult {
  const queryClient = useQueryClient();
  const [failure, setFailure] = useState<ChangeOwnPasswordFailure | null>(null);
  const [succeeded, setSucceeded] = useState(false);

  const mutation = useMutation({
    mutationFn: ({
      currentPassword,
      newPassword,
    }: {
      currentPassword: string;
      newPassword: string;
    }): Promise<CurrentPrincipal> => changeOwnPassword(currentPassword, newPassword),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accessTokensQueryKey });
    },
  });

  const submit = useCallback(
    async (currentPassword: string, newPassword: string): Promise<boolean> => {
      setFailure(null);
      setSucceeded(false);
      try {
        await mutation.mutateAsync({ currentPassword, newPassword });
        setSucceeded(true);
        return true;
      } catch (caught) {
        const validation = parseValidationFailed(caught);
        if (validation) {
          setFailure({ message: validation.message, fieldKey: validation.fieldKey });
        } else {
          setFailure({
            message: caught instanceof Error ? caught.message : "Could not change the password.",
            fieldKey: null,
          });
        }
        return false;
      }
    },
    [mutation],
  );

  return { submit, failure, succeeded, isPending: mutation.isPending };
}
