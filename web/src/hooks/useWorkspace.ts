import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { getWorkspace, type WorkspaceDoc } from "../api/workspace";

export const workspaceQueryKey = ["workspace"] as const;

/** The sidebar's workspace block (`docs/DESIGN.md` 4.3): the deployment's name and
 * its "N people · M agents" line. Readable at `read` scope, so every signed-in caller gets it
 * and the block needs no gate of its own. */
export function useWorkspace(): UseQueryResult<WorkspaceDoc> {
  return useQuery({
    queryKey: workspaceQueryKey,
    queryFn: getWorkspace,
  });
}
