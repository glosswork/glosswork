/**
 * Sign-in by emailed code (change 9), for a workspace whose `GET /api/v1/auth/modes` reports
 * `email_code`. Two steps on one page: an email address and "Send code", then a six-digit code,
 * "Sign in", and "Send another code".
 *
 * **The request's answer is shown verbatim.** The server answers the same sentence whether or not
 * the address can sign in here, so this component adds nothing that could tell the two apart: it
 * moves to the code step on every `202`, and it renders the server's one failure message for every
 * wrong, expired or used code.
 *
 * Submits through `onSubmit` with `event.preventDefault()`, and navigation happens only after the
 * awaited sign-in resolves, as `LoginPage`'s password form does.
 */
import { useState, type FormEvent } from "react";
import { requestSignInCode } from "../api/auth";
import { Button } from "../ui/Button";
import { fieldErrorClass, fieldLabelClass, inputClass } from "../ui/classes";
import { serverMessage } from "./errorMessage";
import { useAuth } from "./useAuth";

export interface CodeSignInFormProps {
  /** Called after a successful sign-in, to send the person on to where they were going. */
  onSignedIn: () => void;
}

export function CodeSignInForm({ onSignedIn }: CodeSignInFormProps) {
  const { loginWithCode } = useAuth();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [sentMessage, setSentMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function sendCode() {
    setError(null);
    setPending(true);
    try {
      const answer = await requestSignInCode(email);
      setSentMessage(answer.message);
      setCode("");
    } catch (err) {
      setError(serverMessage(err, "The code could not be sent. Try again."));
    } finally {
      setPending(false);
    }
  }

  async function handleEmailSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await sendCode();
  }

  async function handleCodeSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      await loginWithCode(email, code.trim());
      onSignedIn();
    } catch (err) {
      setError(serverMessage(err, "That code did not work. Ask for a new code."));
    } finally {
      setPending(false);
    }
  }

  const errorLine = error && (
    <p role="alert" data-testid="login-error" className={fieldErrorClass}>
      {error}
    </p>
  );

  if (sentMessage === null) {
    return (
      <form onSubmit={(event) => void handleEmailSubmit(event)} aria-label="Sign in with a code">
        <div className="mb-3">
          <label htmlFor="code-email" className={fieldLabelClass}>
            Email
          </label>
          <input
            id="code-email"
            data-testid="code-email"
            type="email"
            autoComplete="username"
            className={inputClass}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
        </div>
        {errorLine}
        <Button
          type="submit"
          variant="primary"
          className="w-full justify-center"
          data-testid="code-send"
          disabled={pending}
        >
          {pending ? "Sending…" : "Send code"}
        </Button>
      </form>
    );
  }

  return (
    <form onSubmit={(event) => void handleCodeSubmit(event)} aria-label="Enter your code">
      <p data-testid="code-sent" role="status" className="mb-3 text-sm text-ink-2">
        {sentMessage}
      </p>
      <div className="mb-3">
        <label htmlFor="code-value" className={fieldLabelClass}>
          Code sent to {email}
        </label>
        <input
          id="code-value"
          data-testid="code-value"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]{6}"
          maxLength={6}
          className={inputClass}
          value={code}
          onChange={(event) => setCode(event.target.value)}
          required
        />
      </div>
      {errorLine}
      <Button
        type="submit"
        variant="primary"
        className="w-full justify-center"
        data-testid="code-submit"
        disabled={pending}
      >
        {pending ? "Signing in…" : "Sign in"}
      </Button>
      <Button
        type="button"
        variant="quiet"
        className="mt-2 w-full justify-center"
        data-testid="code-resend"
        disabled={pending}
        onClick={() => void sendCode()}
      >
        Send another code
      </Button>
    </form>
  );
}
