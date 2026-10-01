/**
 * Typed wrappers over the auth surface (`src/glosswork/routes/auth.py`): login,
 * logout, and which login methods this deployment offers. The OIDC leg of the flow is a
 * plain browser navigation (`window.location.href`), not a fetch call, so it has no wrapper
 * here — the provider's redirect and the callback are not something this SPA's own router
 * ever sees.
 */
import { apiRequest } from "./client";
import type { PrincipalRole, Scope } from "./principals";

/** `GET /api/v1/me` and `POST /api/v1/auth/login`'s response (`envelopes.py::me_doc`): the
 * calling principal plus the credential's scope and auth method.
 *
 * `auth_provider` is **required**, not optional: `me_doc` always
 * carries it for a `user`-type principal, and making it required here means a hand-built test
 * fixture that omits it fails typecheck instead of silently taking the OIDC branch of
 * `PasswordPanel`, which reads `null` and `"oidc"` the same way (no local password to change). */
export interface CurrentPrincipal {
  id: string;
  display_name: string;
  email: string | null;
  type: "user" | "service_account";
  role: PrincipalRole;
  scope: Scope;
  auth_method: "session" | "pat";
  auth_provider: "local" | "oidc" | null;
}

/** `GET /api/v1/auth/modes`. `email_code` is true on a hosted workspace that signs people in
 * by emailed code (change 9); `standalone` is then false, because password sign-in is off. */
export interface AuthModes {
  standalone: boolean;
  oidc: boolean;
  email_code: boolean;
}

/** `POST /api/v1/auth/code/request`: always the same sentence, whatever the address. */
export interface CodeRequestAnswer {
  message: string;
}

export function getCurrentPrincipal(): Promise<CurrentPrincipal> {
  return apiRequest<CurrentPrincipal>("/me");
}

export function getAuthModes(): Promise<AuthModes> {
  return apiRequest<AuthModes>("/auth/modes");
}

export function login(email: string, password: string): Promise<CurrentPrincipal> {
  return apiRequest<CurrentPrincipal>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

/** Ask for a sign-in code. The answer never says whether the address can sign in. */
export function requestSignInCode(email: string): Promise<CodeRequestAnswer> {
  return apiRequest<CodeRequestAnswer>("/auth/code/request", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

/** Sign in with an emailed code; answers exactly as `login` does. */
export function verifySignInCode(email: string, code: string): Promise<CurrentPrincipal> {
  return apiRequest<CurrentPrincipal>("/auth/code/verify", {
    method: "POST",
    body: JSON.stringify({ email, code }),
  });
}

export function logout(): Promise<void> {
  return apiRequest<void>("/auth/session", { method: "DELETE" });
}

/** `POST /api/v1/me/password`: a signed-in person's own password change.
 * Session-only and current-password-gated at the service; this wrapper carries no logic
 * of its own, only the shape of the request. */
export function changeOwnPassword(
  currentPassword: string,
  newPassword: string,
): Promise<CurrentPrincipal> {
  return apiRequest<CurrentPrincipal>("/me/password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

/** Navigates the whole browser to the OIDC authorization redirect (FR-I1). Not a fetch call:
 * the provider's login page and the callback's own redirect are outside this SPA's router. */
export function startOidcLogin(): void {
  window.location.href = "/api/v1/auth/oidc/start";
}
