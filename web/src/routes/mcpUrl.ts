/**
 * The MCP URL the first-run screen prints, and the prompt built around it
 * (`docs/DESIGN.md` 8.5 and 7.10).
 *
 * **Why the server answers this and the browser only falls back.** The obvious client-side
 * composition -- this page's origin plus `/mcp` -- is wrong in the configuration the transport's
 * own hardening exists for. `create_app` builds the `/mcp` Host and Origin allowlists from
 * `GW_BASE_URL` (DD-15), so an operator reaching the UI on an origin outside that allowlist
 * would be handed a URL this deployment refuses. The URL's consumer is also the **agent**, not
 * the person reading the screen, and an agent on another machine cannot reach the
 * `http://localhost:8000` a laptop trial is browsed at. `GET /api/v1/workspace` therefore carries
 * `mcp_url`, composed from `GW_BASE_URL`.
 *
 * The fallback is not a guess. `mcp_url` is `null` exactly when `GW_BASE_URL` is unset, which is
 * exactly when the allowlist is empty and the check disables itself -- so on that deployment any
 * origin reaches `/mcp`, and this page's own origin is the one the reader is demonstrably on.
 */

/** The path the MCP surface is served on. A `Route` on the exact path rather than a `Mount`
 * (`src/glosswork/app.py`), so there is no trailing-slash variant and no setting that moves it. */
const MCP_PATH = "/mcp";

/**
 * The URL to print: what the deployment declared, or this page's origin when it declared
 * nothing. `URL` does the joining rather than string concatenation, so a trailing slash on the
 * origin and a non-default port both come out right without a special case each.
 */
export function resolveMcpUrl(serverMcpUrl: string | null | undefined, origin: string): string {
  return serverMcpUrl ?? new URL(MCP_PATH, origin).href;
}

/**
 * The prompt a person pastes into the agent they already use. The copy is the reference still's
 * (`docs/design/counterpart-firstrun-light.png`), unchanged.
 *
 * It is a function of the URL rather than a template two components fill in separately, because
 * the screen renders this string and the Copy button writes it, and those must be the same text
 * by construction.
 *
 * It tells the agent to call `describe_capabilities` first for the reason
 * `docs/AGENT_ONBOARDING.md` section 3 gives: reading the grammar once, early, means never
 * guessing at it while also learning a schema. That tool is `admin`-scoped, which is the right
 * audience here -- the token an administrator mints for themselves on day one.
 */
export function firstRunPrompt(mcpUrl: string): string {
  return (
    `Connect to ${mcpUrl} with the token I gave you. Call describe_capabilities, then ask me ` +
    "what I want to keep track of and build it: real field types, select options with " +
    "descriptions, relations where two things are genuinely different things. Write every " +
    "description for the agent that reads it next."
  );
}

/**
 * The substrings the prompt block sets in the mono face (`docs/DESIGN.md` 7.10: "API names in
 * mono inside it"). Declared beside the prompt rather than at the call site, so a change to the
 * copy and a change to what is emphasised cannot drift apart; `mcpUrl.test.ts` asserts every
 * segment is actually present in the text.
 */
export function FIRST_RUN_PROMPT_MONO(mcpUrl: string): readonly string[] {
  return [mcpUrl, "describe_capabilities"];
}
