/**
 * The context object itself, kept in its own (non-component) module so
 * `AuthProvider.tsx` and `useAuth.ts` can each export only what react-refresh expects
 * a component/hook file to export.
 */
import { createContext } from "react";
import type { CurrentPrincipal } from "../api/auth";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

export interface AuthContextValue {
  status: AuthStatus;
  principal: CurrentPrincipal | null;
  login: (email: string, password: string) => Promise<void>;
  /** Sign in with an emailed code (change 9). */
  loginWithCode: (email: string, code: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
