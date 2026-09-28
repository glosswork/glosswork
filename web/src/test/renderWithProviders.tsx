import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { createQueryClient } from "../app/queryClient";
import { AuthProvider } from "../auth/AuthProvider";
import type { CurrentPrincipal } from "../api/auth";

/**
 * The principal every component test renders as "signed in" unless it overrides `principal`.
 * Its id matches the fixed bootstrap principal id that older fixtures were written against,
 * from before a real session replaced a build-time "who am I" constant — kept identical here so
 * existing "am I the author of this comment" (FR-C5) fixtures did not all need new ids, only a
 * new import.
 */
export const DEFAULT_TEST_PRINCIPAL: CurrentPrincipal = {
  id: "00000000-0000-4000-8000-000000000001",
  display_name: "Test Admin",
  email: "test-admin@example.com",
  type: "user",
  role: "admin",
  scope: "admin",
  auth_method: "session",
  auth_provider: "local",
};

/**
 * Renders a component under the same providers the real app supplies (`main.tsx`): a fresh
 * `QueryClient` per render (so tests never share cached query state), a `MemoryRouter` seeded at
 * `route`, and an `AuthProvider` pre-seeded with `principal` (`DEFAULT_TEST_PRINCIPAL` unless
 * overridden) so no test needs a network layer just to render as signed in. Pass
 * `principal: null` to render the signed-out state instead.
 */
export function renderWithProviders(
  ui: ReactElement,
  {
    route = "/",
    principal = DEFAULT_TEST_PRINCIPAL,
  }: { route?: string; principal?: CurrentPrincipal | null } = {},
) {
  const queryClient = createQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider initialPrincipal={principal}>
        <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}
