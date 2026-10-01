import { defineConfig } from "@playwright/test";
import {
  E2E_ADMIN_EMAIL,
  E2E_ADMIN_PASSWORD,
  E2E_BASE_URL,
  E2E_DATA_DIR,
  E2E_WORKSPACE_NAME,
  E2E_MODEL_DIR,
  CODE_BASE_URL,
  CODE_DATA_DIR,
  CODE_PORT,
  RELAY_BASE_URL,
  RELAY_PORT,
  RELAY_TOKEN,
  E2E_PORT,
  OIDC_ADMIN_GROUP,
  OIDC_CLIENT_ID,
  OIDC_CLIENT_SECRET,
  OIDC_IDENTITY_EMAIL,
  OIDC_IDENTITY_NAME,
  OIDC_ISSUER,
  OIDC_ISSUER_PORT,
  VISUAL_BASE_URL,
  VISUAL_DATA_DIR,
  VISUAL_PORT,
} from "./e2e/constants";

/**
 * The end-to-end scenarios run against the REAL built frontend (`npm run build` -> `web/dist`)
 * served by a REAL `uvicorn` process reading a REAL, freshly seeded SQLite database — never a
 * dev server, never a mocked backend.
 *
 * The browser has no bearer-token path: there is no build-time PAT, no acknowledgement flag and no
 * build or startup guard for one, backend or frontend, and the built bundle carries no credential
 * at all. The browser authenticates the same way a real user would, through a server-side session
 * cookie (DD-9) set by `POST /api/v1/auth/login`, which every spec obtains by driving the real
 * `/login` form (`e2e/constants.ts::signInAsE2eAdmin`) before it starts asserting anything about
 * the UI.
 *
 * Two credentials feed this run, both produced by `e2e/constants.ts::prepareE2eCredential()`
 * before `webServer` starts:
 *
 *   - a local admin account (`E2E_ADMIN_EMAIL` / `E2E_ADMIN_PASSWORD`), created directly via the
 *     operator CLI (`python -m glosswork.admin create-admin`) against this run's own data
 *     directory. It is also passed through below as `GW_BOOTSTRAP_ADMIN_EMAIL` /
 *     `GW_BOOTSTRAP_ADMIN_PASSWORD` — the ordinary deployment path — but by the time uvicorn
 *     starts the account already exists, so `_ensure_bootstrap_admin` is a no-op; the CLI
 *     creates it first specifically so the run can read back its real principal id
 *     (`E2E_PRINCIPAL_ID`) before any spec runs.
 *   - a PAT minted for that same principal (`E2E_AUTH_HEADER`), used only by the specs' own
 *     server-to-server REST calls that seed fixture data or race a concurrent write — never by
 *     the browser. PATs are not deprecated; the browser simply never uses one.
 *
 * `GW_COOKIE_SECURE=false` because this suite serves plain HTTP on localhost, the same
 * fail-closed knob a developer running a local plain-HTTP deployment sets deliberately
 * (`src/glosswork/config.py::Settings.cookie_secure`).
 *
 * `webServer` is an array of two processes: `e2e/fake-idp.mjs`, a minimal local OIDC provider
 * (see `e2e/constants.ts` for why it exists), started first; and the app server itself, run in
 * `GW_AUTH_MODE=both` so the standalone specs and `auth.spec.ts`'s OIDC scenario share one
 * server and one database.
 *
 * The app server's `command` below:
 *   1. builds the frontend (`npm run build`, run with this config's own `web/` cwd) so
 *      `web/dist` exists for `src/glosswork/app.py`'s static-file fallback to serve;
 *   2. moves to the repo root (`uv run uvicorn` resolves `web/dist` and the `.venv` relative
 *      to the repo root, per `AGENTS.md`'s "Run locally" command row);
 *   3. starts uvicorn against the same `GW_DATA_DIR` the admin account and PAT were created in,
 *      on a port distinct from the normal dev port 8000.
 *
 * `reuseExistingServer: false` is deliberate, not the usual CI-only default: the spec seeds
 * its own object type and records over the REST API, so reusing a server left over from a
 * previous run (with that object type already created) would break the seeding step — and it
 * would also be holding a database the current run's token and admin account were never created
 * in.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  reporter: "list",
  /**
   * One retry, for one specific, diagnosed cause — NOT to paper over flaky tests.
   *
   * Observed on `main` 2026-08-24: a full run died before any test executed, with
   * `Error: Unexpected module status 3` / `ERR_INTERNAL_ASSERTION` from Node 22.17.1's
   * `require(esm)` path loading `@testing-library/dom`, killing the worker process. The next
   * four consecutive full runs passed. It is a Node internals bug, not a product regression,
   * but it lands in the suite that gates every merge, where an unexplained red is expensive and
   * will eventually be chased as real. `.nvmrc` and `engines.node` pin the toolchain version;
   * this retries the worker death rather than failing the run on it.
   *
   * This must not hide genuine flakiness: Playwright reports a spec that passed only on retry
   * as "flaky", not as passed, and a *test-level* flake remains a bug to chase rather than
   * something this absorbs.
   */
  retries: 1,
  use: {
    baseURL: E2E_BASE_URL,
  },
  /**
   * Two projects, two servers: the functional specs share one server and database
   * as they always have, while the visual-regression spec gets its own — screenshot baselines
   * embed global page state (which object types exist, how far the header wraps), and on the
   * shared database that state depends on which racing worker seeded what first.
   */
  projects: [
    { name: "e2e", testIgnore: /ui-visual\.spec\.ts/ },
    { name: "visual", testMatch: /ui-visual\.spec\.ts/, use: { baseURL: VISUAL_BASE_URL } },
  ],
  expect: {
    /** A hair of tolerance for GPU/antialiasing jitter on the visual baselines
     * (`ui-visual.spec.ts`); everything else about a screenshot mismatch should
     * fail loudly. */
    toHaveScreenshot: { maxDiffPixelRatio: 0.001 },
  },
  webServer: [
    {
      // The local OIDC provider (`e2e/fake-idp.mjs`), started before and independently of the
      // app server: `GW_AUTH_MODE=both` below makes the app dial out to it at OIDC-login time
      // and at ID-token-verification time (JWKS discovery), so it must already be listening.
      command: `node e2e/fake-idp.mjs`,
      url: `${OIDC_ISSUER}/.well-known/openid-configuration`,
      reuseExistingServer: false,
      timeout: 30_000,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        FAKE_IDP_PORT: String(OIDC_ISSUER_PORT),
        OIDC_CLIENT_ID,
        OIDC_ADMIN_GROUP,
        OIDC_IDENTITY_EMAIL,
        OIDC_IDENTITY_NAME,
      },
    },
    {
      command: `bash -c "npm run build && cd .. && uv run uvicorn glosswork.app:app --port ${E2E_PORT}"`,
      url: `${E2E_BASE_URL}/healthz`,
      reuseExistingServer: false,
      timeout: 180_000,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        GW_DATA_DIR: E2E_DATA_DIR,
        // **Raised for the suite, not for a deployment.** `login_ip_max_attempts`
        // defaults to 60 in a 300-second window, and every worker signs in from 127.0.0.1, so
        // the whole run shares one budget: at 61 specs the suite sat one sign-in under its own
        // ceiling, and `/activity`'s four tipped it over. The failure does not look like a rate
        // limit -- the login POST returns 429, the shell never renders, and four unrelated
        // specs report `current-principal` missing -- so it reads as an application bug in
        // whatever changed last. The per-account budget stays at its default; this is the
        // per-source one, which exists for an office behind a proxy and means nothing when the
        // source is the test runner itself.
        GW_LOGIN_IP_MAX_ATTEMPTS: "1000",
        // Embedding stays ON for this run: the app server loads the real bundled
        // model from the source checkout and starts the worker thread, so these specs
        // exercise the configuration a deployment actually runs. Fetch it once with
        // `uv run python scripts/fetch_model.py`; startup fails fast naming
        // GW_MODEL_DIR if it is missing.
        GW_MODEL_DIR: E2E_MODEL_DIR,
        GW_BOOTSTRAP_ADMIN_EMAIL: E2E_ADMIN_EMAIL,
        GW_BOOTSTRAP_ADMIN_PASSWORD: E2E_ADMIN_PASSWORD,
        GW_COOKIE_SECURE: "false",
        // `both`, not `oidc`: the standalone-login specs (table-end-to-end.spec.ts,
        // schema-editor-end-to-end.spec.ts, settings-proposal-approval.spec.ts) keep signing in
        // with the local admin account; only auth.spec.ts's OIDC test exercises this leg.
        GW_AUTH_MODE: "both",
        GW_BASE_URL: E2E_BASE_URL,
        // The sidebar's workspace name comes from this setting, and an unset one
        // renders the mark alone. Both servers name their workspace so the setting is exercised
        // end to end rather than only in the backend's own tests.
        GW_WORKSPACE_NAME: E2E_WORKSPACE_NAME,
        GW_OIDC_ISSUER: OIDC_ISSUER,
        GW_OIDC_CLIENT_ID: OIDC_CLIENT_ID,
        GW_OIDC_CLIENT_SECRET: OIDC_CLIENT_SECRET,
        GW_OIDC_ADMIN_GROUPS: OIDC_ADMIN_GROUP,
      },
    },
    {
      // The visual project's own server: identical configuration, its own data
      // directory and port, so every screenshot is a function of ui-visual.spec.ts's fixtures
      // alone. The frontend was already built by the entry above; `npm run build` here is a
      // near-no-op rerun kept for the degenerate case where only this server starts.
      command: `bash -c "npm run build && cd .. && uv run uvicorn glosswork.app:app --port ${VISUAL_PORT}"`,
      url: `${VISUAL_BASE_URL}/healthz`,
      reuseExistingServer: false,
      timeout: 180_000,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        GW_DATA_DIR: VISUAL_DATA_DIR,
        // Same reason as the functional server above: one source IP for the whole run.
        GW_LOGIN_IP_MAX_ATTEMPTS: "1000",
        GW_MODEL_DIR: E2E_MODEL_DIR,
        GW_BOOTSTRAP_ADMIN_EMAIL: E2E_ADMIN_EMAIL,
        GW_BOOTSTRAP_ADMIN_PASSWORD: E2E_ADMIN_PASSWORD,
        GW_COOKIE_SECURE: "false",
        GW_AUTH_MODE: "both",
        GW_BASE_URL: VISUAL_BASE_URL,
        GW_WORKSPACE_NAME: E2E_WORKSPACE_NAME,
        GW_OIDC_ISSUER: OIDC_ISSUER,
        GW_OIDC_CLIENT_ID: OIDC_CLIENT_ID,
        GW_OIDC_CLIENT_SECRET: OIDC_CLIENT_SECRET,
        GW_OIDC_ADMIN_GROUPS: OIDC_ADMIN_GROUP,
      },
    },
    {
      // The fake relay (change 9), the one enforcer of the relay request's definition, run as a
      // process. `GET /sent` is its test-only record of every message it accepted, which is
      // how `email-code.spec.ts` reads the code a person would read in their inbox.
      command: `bash -c "cd .. && uv run python -m tests.fake_relay --port ${RELAY_PORT} --token ${RELAY_TOKEN}"`,
      url: `${RELAY_BASE_URL}/sent`,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      // A hosted workspace: sign-in by emailed code through the fake relay above. Embedding is
      // off because nothing here searches, and this server's only spec is
      // `email-code.spec.ts`. The visual project never reaches it, so no baseline repaints.
      command: `bash -c "npm run build && cd .. && uv run uvicorn glosswork.app:app --port ${CODE_PORT}"`,
      url: `${CODE_BASE_URL}/healthz`,
      reuseExistingServer: false,
      timeout: 180_000,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        GW_DATA_DIR: CODE_DATA_DIR,
        GW_LOGIN_IP_MAX_ATTEMPTS: "1000",
        GW_EMBEDDING_ENABLED: "false",
        GW_BOOTSTRAP_ADMIN_EMAIL: E2E_ADMIN_EMAIL,
        GW_BOOTSTRAP_ADMIN_PASSWORD: E2E_ADMIN_PASSWORD,
        GW_COOKIE_SECURE: "false",
        GW_AUTH_MODE: "standalone",
        GW_BASE_URL: CODE_BASE_URL,
        GW_WORKSPACE_NAME: E2E_WORKSPACE_NAME,
        GW_RELAY_URL: `${RELAY_BASE_URL}/v1/relay/send`,
        GW_RELAY_TOKEN: RELAY_TOKEN,
      },
    },
  ],
});
