/**
 * The login screen (FR-I1): a signed-out visitor lands here instead of a raw 401
 * (`RequireAuth`), picks a local password login or "Sign in with Okta" depending on
 * `GET /api/v1/auth/modes`, and on success is sent back to whatever route they originally
 * asked for (`location.state.from`, set by `RequireAuth`).
 *
 * A hosted workspace whose modes report `email_code` gets `CodeSignInForm` instead of the
 * password form (change 9). **While `modes` is loading the page renders no form at all**, so
 * a code workspace never flashes a password form it does not accept.
 */
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate, type Location } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import { getAuthModes, startOidcLogin } from "../api/auth";
import { Button } from "../ui/Button";
import { fieldErrorClass, fieldLabelClass, inputClass } from "../ui/classes";
import { useAuth } from "./useAuth";
import { Wordmark } from "../brand/Wordmark";
import { CodeSignInForm } from "./CodeSignInForm";

export function LoginPage() {
  const { status, login } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const { data: modes } = useQuery({ queryKey: ["auth-modes"], queryFn: getAuthModes });

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Already signed in (e.g. a stale tab navigated back here): go straight to the originally
  // requested route, or the home page.
  if (status === "authenticated") {
    const from = (location.state as { from?: Location } | null)?.from;
    return <Navigate to={from?.pathname ?? "/"} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      const from = (location.state as { from?: Location } | null)?.from;
      navigate(from?.pathname ?? "/", { replace: true });
    } catch (err) {
      // `AuthService` already makes the unknown-email and wrong-password branches
      // indistinguishable at the service layer; this renders whatever message the server sent
      // without adding a client-side distinction of its own.
      if (err instanceof ApiError) {
        try {
          const body = JSON.parse(err.body) as { error?: { message?: string } };
          setError(body.error?.message ?? "Sign-in failed. Check your email and password.");
        } catch {
          setError("Sign-in failed. Check your email and password.");
        }
      } else {
        setError("Sign-in failed. Check your email and password.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  function goOn() {
    const from = (location.state as { from?: Location } | null)?.from;
    navigate(from?.pathname ?? "/", { replace: true });
  }

  const showCode = modes?.email_code === true;
  const showStandalone = modes?.standalone === true && !showCode;
  const showOidc = modes?.oidc === true;

  return (
    <div className="flex min-h-screen items-center justify-center bg-ground px-4">
      <div className="w-full max-w-sm rounded-card border border-line bg-surface p-6 shadow-sm">
        <h1 className="mb-4 flex justify-center">
          <Wordmark size={40} />
        </h1>

        {showCode && <CodeSignInForm onSignedIn={goOn} />}

        {showStandalone && (
          <form onSubmit={(event) => void handleSubmit(event)} aria-label="Sign in">
            <div className="mb-3">
              <label htmlFor="login-email" className={fieldLabelClass}>
                Email
              </label>
              <input
                id="login-email"
                data-testid="login-email"
                type="email"
                autoComplete="username"
                className={inputClass}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
            </div>
            <div className="mb-3">
              <label htmlFor="login-password" className={fieldLabelClass}>
                Password
              </label>
              <input
                id="login-password"
                data-testid="login-password"
                type="password"
                autoComplete="current-password"
                className={inputClass}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </div>
            {error && (
              <p role="alert" data-testid="login-error" className={fieldErrorClass}>
                {error}
              </p>
            )}
            <Button
              type="submit"
              variant="primary"
              className="w-full justify-center"
              data-testid="login-submit"
              disabled={submitting}
            >
              {submitting ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        )}

        {(showStandalone || showCode) && showOidc && (
          <p className="my-3 text-center text-xs text-ink-2">or</p>
        )}

        {showOidc && (
          <Button
            type="button"
            variant="secondary"
            className="w-full justify-center"
            data-testid="login-oidc"
            onClick={startOidcLogin}
          >
            Sign in with Okta
          </Button>
        )}
      </div>
    </div>
  );
}
