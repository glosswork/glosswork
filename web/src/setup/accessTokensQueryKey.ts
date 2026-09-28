/**
 * `AccessTokensPanel`'s query key, in its own module rather than exported
 * from `AccessTokensPanel.tsx` itself: that file also exports a component, and
 * `react-refresh/only-export-components` refuses a second export from a file Fast Refresh
 * swaps -- the same reason `hooks/useObjectTypes.ts` holds `objectTypesQueryKey` beside its hook
 * rather than inside the component that first read it.
 *
 * `useChangeOwnPassword` invalidates this key on a successful self-change: it revokes every
 * personal access token the principal holds in that same request, and the card above would
 * otherwise keep showing them "Active" until some unrelated re-render happened to refetch it.
 */
export const accessTokensQueryKey = ["access-tokens", "own"] as const;
