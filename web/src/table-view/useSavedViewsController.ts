/**
 * Saved-views data access for the table view: lists views for one object type and wraps the
 * create/update mutations. Selection state (which view is currently loaded) and "apply this
 * view's config to my local state" logic live in `TableView.tsx`/`tableViewConfig.ts` — this
 * hook only talks to the API and keeps the list fresh.
 */
import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import {
  createSavedView,
  listSavedViews,
  updateSavedView,
  type SavedView,
} from "../api/savedViews";
import type { TableViewConfig } from "./types";

export function savedViewsQueryKey(objectTypeKey: string) {
  return ["saved-views", objectTypeKey] as const;
}

export function useSavedViewsList(objectTypeKey: string): UseQueryResult<SavedView[]> {
  return useQuery({
    queryKey: savedViewsQueryKey(objectTypeKey),
    queryFn: () => listSavedViews(objectTypeKey),
  });
}

export interface UseSavedViewMutationsResult {
  saveAsNew: (name: string, config: TableViewConfig, isDefault: boolean) => Promise<SavedView>;
  saveExisting: (viewId: string, config: TableViewConfig) => Promise<SavedView>;
  setDefault: (viewId: string) => Promise<SavedView>;
}

export function useSavedViewMutations(objectTypeKey: string): UseSavedViewMutationsResult {
  const queryClient = useQueryClient();
  const invalidate = useCallback(
    () => queryClient.invalidateQueries({ queryKey: savedViewsQueryKey(objectTypeKey) }),
    [objectTypeKey, queryClient],
  );

  const createMutation = useMutation({
    mutationFn: ({ name, config, isDefault }: { name: string; config: TableViewConfig; isDefault: boolean }) =>
      createSavedView(objectTypeKey, {
        name,
        config: config as unknown as Record<string, unknown>,
        mode: config.mode,
        is_default: isDefault,
      }),
    onSuccess: () => void invalidate(),
  });

  const updateMutation = useMutation({
    mutationFn: ({ viewId, config }: { viewId: string; config: TableViewConfig }) =>
      updateSavedView(viewId, {
        config: config as unknown as Record<string, unknown>,
        mode: config.mode,
      }),
    onSuccess: () => void invalidate(),
  });

  const defaultMutation = useMutation({
    mutationFn: (viewId: string) => updateSavedView(viewId, { is_default: true }),
    onSuccess: () => void invalidate(),
  });

  return {
    saveAsNew: (name, config, isDefault) => createMutation.mutateAsync({ name, config, isDefault }),
    saveExisting: (viewId, config) => updateMutation.mutateAsync({ viewId, config }),
    setDefault: (viewId) => defaultMutation.mutateAsync(viewId),
  };
}
