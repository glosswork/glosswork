/**
 * Who is signed in, and the actions that change it (FR-A3, DD-9).
 *
 * On mount, asks `GET /api/v1/me`: a `401` means no session cookie resolved (or none was
 * presented), which is the ordinary signed-out state, not an error to surface. `login` and
 * `logout` call the auth routes and update this context's state directly from their
 * response rather than re-fetching `/me`, so there is no window where the UI's idea of "who am
 * I" lags the request that just changed it.
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  getCurrentPrincipal,
  login as loginRequest,
  logout as logoutRequest,
  verifySignInCode,
  type CurrentPrincipal,
} from "../api/auth";
import { AuthContext, type AuthContextValue, type AuthStatus } from "./authContext";

export interface AuthProviderProps {
  children: ReactNode;
  /** Test-only escape hatch: seeds the context with a principal and skips the `/me` fetch
   * entirely, so component tests do not need a network layer just to render as "signed in". */
  initialPrincipal?: CurrentPrincipal | null;
}

export function AuthProvider({ children, initialPrincipal }: AuthProviderProps) {
  const [status, setStatus] = useState<AuthStatus>(
    initialPrincipal !== undefined
      ? initialPrincipal
        ? "authenticated"
        : "unauthenticated"
      : "loading",
  );
  const [principal, setPrincipal] = useState<CurrentPrincipal | null>(initialPrincipal ?? null);

  useEffect(() => {
    if (initialPrincipal !== undefined) {
      return; // seeded for a test; never fetches.
    }
    let cancelled = false;
    getCurrentPrincipal()
      .then((me) => {
        if (!cancelled) {
          setPrincipal(me);
          setStatus("authenticated");
        }
      })
      .catch(() => {
        if (cancelled) return;
        // A 401 (no session resolved) is the ordinary signed-out state; an unexpected
        // failure (network error, 500) is treated the same way for navigation purposes —
        // there is no principal to show either way, and the login screen is where a retry
        // belongs.
        setStatus("unauthenticated");
      });
    return () => {
      cancelled = true;
    };
  }, [initialPrincipal]);

  const login = useCallback(async (email: string, password: string) => {
    const me = await loginRequest(email, password);
    setPrincipal(me);
    setStatus("authenticated");
  }, []);

  const loginWithCode = useCallback(async (email: string, code: string) => {
    const me = await verifySignInCode(email, code);
    setPrincipal(me);
    setStatus("authenticated");
  }, []);

  const logout = useCallback(async () => {
    await logoutRequest();
    setPrincipal(null);
    setStatus("unauthenticated");
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, principal, login, loginWithCode, logout }),
    [status, principal, login, loginWithCode, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
