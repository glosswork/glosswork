import { describe, expect, it } from "vitest";
import { FIRST_RUN_PROMPT_MONO, firstRunPrompt, resolveMcpUrl } from "./mcpUrl";

/**
 * The URL an agent is told to connect to, and the prompt built around it.
 *
 * The two functions are separate because they fail differently: `resolveMcpUrl` is about a
 * deployment's configuration and `firstRunPrompt` is about copy. Keeping the prompt a function
 * of the URL rather than a template two call sites fill in is what makes the rule -- the
 * displayed text and the copied text are one string -- true by construction rather than by
 * two components agreeing.
 */
describe("resolveMcpUrl", () => {
  it("uses the server's mcp_url when the deployment declared one", () => {
    expect(resolveMcpUrl("https://northwind.glosswork.app/mcp", "https://elsewhere.example")).toBe(
      "https://northwind.glosswork.app/mcp",
    );
  });

  it("falls back to the browser's own origin when the server has no base URL", () => {
    // Null means GW_BASE_URL is unset, which is exactly the case where the /mcp transport's
    // host allowlist is empty and the check disables itself, so the origin works.
    expect(resolveMcpUrl(null, "https://tracker.acme.example")).toBe(
      "https://tracker.acme.example/mcp",
    );
  });

  it("does not double the separator when the origin carries a trailing slash", () => {
    expect(resolveMcpUrl(null, "https://tracker.acme.example/")).toBe(
      "https://tracker.acme.example/mcp",
    );
  });

  it("keeps a non-default port, which a local deployment always has", () => {
    expect(resolveMcpUrl(null, "http://127.0.0.1:8000")).toBe("http://127.0.0.1:8000/mcp");
  });
});

describe("firstRunPrompt", () => {
  const url = "https://northwind.glosswork.app/mcp";

  it("names the deployment's URL exactly once", () => {
    const occurrences = firstRunPrompt(url).split(url).length - 1;
    expect(occurrences).toBe(1);
  });

  it("tells the agent to read the schema before it writes one", () => {
    // docs/AGENT_ONBOARDING.md section 3: describe_capabilities is the first call of a session.
    expect(firstRunPrompt(url)).toContain("describe_capabilities");
  });

  it("asks for descriptions, which is the thing the product is named for", () => {
    expect(firstRunPrompt(url)).toContain("Write every description for the agent that reads it next.");
  });

  it("keeps the voice: no jargon docs/DESIGN.md 5 forbids, and no exclamation marks", () => {
    const prompt = firstRunPrompt(url);
    for (const forbidden of ["AI", "assistant", "automation", "smart", "magic", "!"]) {
      expect(prompt).not.toContain(forbidden);
    }
  });

  it("every mono segment it declares is actually in the text it produces", () => {
    // The prompt block renders these in the mono face by splitting the string on them
    // (docs/DESIGN.md 7.10: "API names in mono inside it"). A segment that is not present
    // would silently render nothing, so the list and the copy cannot drift apart.
    const prompt = firstRunPrompt(url);
    for (const segment of FIRST_RUN_PROMPT_MONO(url)) {
      expect(prompt).toContain(segment);
    }
  });
});
