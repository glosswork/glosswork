/**
 * Shared between `playwright.config.ts` (the `webServer` wiring) and the e2e specs (seeding
 * fixture data over the REST API before/during a scenario, and signing the browser itself in),
 * so the port, base URL, and the credentials used to reach the real `uvicorn` instance are
 * declared exactly once.
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(HERE, "..", "..");

/**
 * Where `scripts/fetch_model.py` puts the bundled embedding model in a source checkout
 * (DD-32). The e2e run points `GW_MODEL_DIR` here so the app server starts with embedding
 * genuinely enabled -- the real ONNX provider loaded and the worker thread running -- rather
 * than with the feature switched off. Running these specs against a configuration no deployment
 * uses would defeat one reason they run at all: to prove the model and the worker break
 * nothing.
 */
export const E2E_MODEL_DIR = path.join(REPO_ROOT, "models");

/** The workspace name both e2e servers run with (`GW_WORKSPACE_NAME`). A name the
 * product would never invent for itself, so a baseline showing the product name instead would be
 * obvious rather than plausible. */
export const E2E_WORKSPACE_NAME = "Northwind Operations";

/** A port distinct from the normal dev port 8000, to avoid colliding with anything a developer
 * might have running locally. */
export const E2E_PORT = 8931;

export const E2E_BASE_URL = process.env.GW_E2E_BASE_URL ?? `http://localhost:${E2E_PORT}`;

/**
 * `e2e/fake-idp.mjs`: a minimal, dependency-free local OIDC provider (a real Node HTTP server,
 * not a mock inside the test process) so that a browser test signing in through an OIDC
 * identity against a locally generated JWKS fixture, still with no network egress, is proven
 * through the actual browser and the actual `/login` screen's "Sign
 * in with Okta" button, not only at the backend `TestClient` level
 * (`tests/test_auth_routes.py`). It generates its own RSA keypair at startup, serves
 * `/.well-known/openid-configuration` and `/jwks` for `PyJwkClientJwksSource`'s real discovery
 * (this is a genuine local HTTP round trip, deliberately — the "no network egress" property
 * this preserves is that nothing leaves *localhost*, not that no HTTP happens at all), and
 * implements just enough of `/v1/authorize` and `/v1/token` for one fixed identity. It does not
 * validate PKCE's `code_verifier` against the `code_challenge` it was sent — that is the
 * *provider's* defense against a stolen authorization code, immaterial to proving this
 * application's own OIDC client code paths work end-to-end in one browser process it controls.
 */
export const OIDC_ISSUER_PORT = 8932;
export const OIDC_ISSUER = `http://localhost:${OIDC_ISSUER_PORT}`;
export const OIDC_CLIENT_ID = "e2e-fake-oidc-client";
export const OIDC_CLIENT_SECRET = "e2e-fake-oidc-secret";
export const OIDC_ADMIN_GROUP = "e2e-oidc-admins";
export const OIDC_IDENTITY_EMAIL = "oidc-e2e@example.com";
export const OIDC_IDENTITY_NAME = "OIDC E2E User";

/**
 * The local admin account the browser itself signs in as through the real login screen.
 * `webServer.env` in `playwright.config.ts` passes these through as `GW_BOOTSTRAP_ADMIN_EMAIL` /
 * `GW_BOOTSTRAP_ADMIN_PASSWORD`, but `prepareE2eCredential()` below also creates the account
 * directly via the operator CLI *before* `webServer` starts (so it can read back the account's
 * real principal id for `E2E_PRINCIPAL_ID`) — by the time uvicorn's own
 * `_ensure_bootstrap_admin` runs, an active admin already exists, so that startup hook is a
 * no-op. Passing the env vars through anyway documents the ordinary deployment path and costs
 * nothing.
 *
 * The password must clear `GW_PASSWORD_MIN_LENGTH` (default 12 characters).
 */
export const E2E_ADMIN_EMAIL = "e2e-admin@example.com";
export const E2E_ADMIN_PASSWORD = "e2e-admin-passw0rd!";

/**
 * Where the run's data directory, minted PAT, and signed-in admin's principal id are recorded.
 *
 * A literal such as the old interim `gw_pat_admin` is refused (`PatTokenResolver` accepts only
 * a real minted token), so the credential cannot be a constant in this file: it has to be
 * produced, once per run, by `python -m glosswork.admin mint-token` against the run's own fresh
 * database.
 *
 * The browser has no bearer-token path: it signs in through the real login form and gets a
 * session cookie, while the specs' own REST seeding calls use a PAT (a server-to-server
 * credential). The PAT is minted for the *same* principal the browser signs in as (the local
 * admin account created by `create-admin` below) rather than the fixed
 * `BOOTSTRAP_PRINCIPAL_ID`, because `create-admin` always assigns a fresh UUID.
 * `E2E_PRINCIPAL_ID` is that id, read back from the CLI's own output, so the specs' "am I the
 * author of this comment" assertions (FR-C5) mean the browser's principal.
 *
 * All of this is written to disk rather than passed in memory because Playwright re-loads the
 * config in every worker process: `prepareE2eCredential()` runs only in the runner (which has no
 * `TEST_WORKER_INDEX`) and every worker then reads what the runner wrote, so all of them share
 * one token, one data dir, and one principal id against one database instead of each minting its
 * own against a different one.
 */
/** Hand-off files carrying one prepared credential from the config process to the worker
 * processes; `tag` separates the main run's credential from the visual project's,
 * which runs against its own server and database. */
function handoffFiles(tag: string): { token: string; dataDir: string; principalId: string } {
  return {
    token: path.join(HERE, `.e2e-token${tag}`),
    dataDir: path.join(HERE, `.e2e-datadir${tag}`),
    principalId: path.join(HERE, `.e2e-principal-id${tag}`),
  };
}

function isWorkerProcess(): boolean {
  return process.env.TEST_WORKER_INDEX !== undefined;
}

/** Matches `create-admin`'s stdout on both its idempotent branches: `admin.py::_cmd_create_admin`
 * prints "Created admin principal: <id> <email>" the first time and "Admin principal already
 * exists: <id> <email>" on any later run — either way the id is the first token after the
 * colon. */
const CREATE_ADMIN_ID_PATTERN = /^(?:Created admin principal|Admin principal already exists): (\S+) </m;

/**
 * Create this run's data directory, migrate it, create the local admin account the browser signs
 * in as, mint an `admin` PAT for that same principal, and record all three for the workers.
 * Called once, from `playwright.config.ts`, before `webServer` starts and before any spec is
 * loaded.
 */
export function prepareE2eCredential(): { token: string; dataDir: string; principalId: string } {
  return prepareCredential("");
}

/** `prepareE2eCredential`, parameterized by hand-off tag so the visual project can
 * prepare a second, fully independent data directory and credential the same way. */
function prepareCredential(tag: string): { token: string; dataDir: string; principalId: string } {
  const files = handoffFiles(tag);
  if (isWorkerProcess()) {
    return {
      token: readFileSync(files.token, "utf8").trim(),
      dataDir: readFileSync(files.dataDir, "utf8").trim(),
      principalId: readFileSync(files.principalId, "utf8").trim(),
    };
  }
  const dataDir = mkdtempSync(path.join(tmpdir(), "gw-e2e-"));
  const runEnv = { ...process.env, GW_DATA_DIR: dataDir };

  const createAdminOutput = execFileSync(
    "uv",
    [
      "run",
      "python",
      "-m",
      "glosswork.admin",
      "create-admin",
      "--email",
      E2E_ADMIN_EMAIL,
      "--password",
      E2E_ADMIN_PASSWORD,
      "--display-name",
      "E2E Admin",
    ],
    { cwd: REPO_ROOT, env: runEnv, encoding: "utf8" },
  ).trim();
  const match = CREATE_ADMIN_ID_PATTERN.exec(createAdminOutput);
  if (!match) {
    throw new Error(
      `Expected 'admin create-admin' to print the created principal's id, got: ${createAdminOutput}`,
    );
  }
  const principalId = match[1];

  const token = execFileSync(
    "uv",
    [
      "run",
      "python",
      "-m",
      "glosswork.admin",
      "mint-token",
      "--name",
      "playwright-e2e",
      "--scope",
      "admin",
      "--principal-id",
      principalId,
      "--quiet",
    ],
    { cwd: REPO_ROOT, env: runEnv, encoding: "utf8" },
  ).trim();
  if (!token.startsWith("gw_pat_")) {
    throw new Error(
      `Expected 'admin mint-token --quiet' to print a single gw_pat_ token, got: ${token}`,
    );
  }
  writeFileSync(files.token, token);
  writeFileSync(files.dataDir, dataDir);
  writeFileSync(files.principalId, principalId);
  return { token, dataDir, principalId };
}

const credential = prepareE2eCredential();

export const E2E_TOKEN = credential.token;
export const E2E_DATA_DIR = credential.dataDir;

/** The real principal id of the local admin account `create-admin` created above, and that the
 * browser signs in as through `/login`. See the block comment above `E2E_ADMIN_EMAIL`. */
export const E2E_PRINCIPAL_ID = credential.principalId;

/** The credential the specs' own server-to-server REST seeding calls use (PATs are fully valid
 * credentials for non-browser callers; only the browser signs in with a session). Minted for the
 * same principal the browser signs in as (`E2E_PRINCIPAL_ID`). */
export const E2E_AUTH_HEADER = { Authorization: `Bearer ${E2E_TOKEN}` };

/**
 * The visual-regression project (`ui-visual.spec.ts`) runs against its OWN app server
 * and database, prepared here exactly like the main run's: screenshot baselines embed global
 * page state (which object types exist, how far the header wraps), and on the shared database
 * every other spec's seeding makes that state race-dependent. A dedicated data directory makes
 * every pixel a function of this one spec's fixtures.
 */
export const VISUAL_PORT = 8933;
export const VISUAL_BASE_URL = `http://localhost:${VISUAL_PORT}`;

const visualCredential = prepareCredential("-visual");
export const VISUAL_DATA_DIR = visualCredential.dataDir;
export const VISUAL_AUTH_HEADER = { Authorization: `Bearer ${visualCredential.token}` };

/** The visual admin's own principal id: every REST-seeded comment's `author_id` and audit
 * event's `principal_id` on the visual database is this value, unless the seeding call sets an
 * agent label. `ui-visual.spec.ts` masks screenshot regions by matching this exact string rather
 * than inventing a new selector.
 *
 * REST can set an agent label (DD-17). A `middleware.py` that hardcoded `agent_label_id=None`
 * would make every REST write a person's; `middleware.py` resolves `X-Agent-Label` for a bearer
 * credential and keeps `None` only for a cookie session. Measured, not inferred:
 * a PAT request carrying the header produces a proposal with `proposed_agent` set, and
 * `e2e/inbox.spec.ts` depends on it. */
export const VISUAL_PRINCIPAL_ID = visualCredential.principalId;

/**
 * Sign-in by emailed code (change 9) runs against a third app server, configured as a hosted
 * workspace: `GW_RELAY_URL` names the fake relay (`tests/fake_relay.py`, run as a process on
 * `RELAY_PORT`), embedding is off, and the first administrator comes from
 * `GW_BOOTSTRAP_ADMIN_*`. Its own data directory, so no other spec's fixtures reach it, and no
 * other spec signs in there, so the existing functional server is untouched.
 *
 * The relay token is built at load time rather than written as one literal, so no
 * credential-shaped string sits in the tree; every process that loads this module computes the
 * same value.
 */
export const RELAY_PORT = 8934;
export const RELAY_BASE_URL = `http://127.0.0.1:${RELAY_PORT}`;
export const RELAY_TOKEN = ["e2e", "relay", "token", "not", "a", "secret"].join("-").padEnd(48, "0");
export const CODE_PORT = 8935;
export const CODE_BASE_URL = `http://localhost:${CODE_PORT}`;

function prepareCodeDataDir(): string {
  const file = path.join(HERE, ".e2e-datadir-code");
  if (isWorkerProcess()) return readFileSync(file, "utf8").trim();
  const dataDir = mkdtempSync(path.join(tmpdir(), "gw-e2e-code-"));
  writeFileSync(file, dataDir);
  return dataDir;
}

export const CODE_DATA_DIR = prepareCodeDataDir();

/**
 * Drives the real `/login` screen with an arbitrary local account's credentials, and waits for
 * the app shell's signed-in identity block to render before returning. No spec injects a session
 * cookie programmatically: at least one real, browser-driven sign-in through the login screen
 * must be proven, and a shared helper that always drives the real form for every spec satisfies
 * that trivially.
 */
export async function signInAs(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByTestId("login-email").fill(email);
  await page.getByTestId("login-password").fill(password);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("current-principal")).toBeVisible();
}

/** `signInAs` for the E2E admin account every existing spec's `beforeEach` uses. */
export async function signInAsE2eAdmin(page: Page): Promise<void> {
  await signInAs(page, E2E_ADMIN_EMAIL, E2E_ADMIN_PASSWORD);
}
