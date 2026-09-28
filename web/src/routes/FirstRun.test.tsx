import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { FirstRun } from "./FirstRun";
import { renderWithProviders } from "../test/renderWithProviders";

/**
 * `docs/DESIGN.md` 8.5, the screen a fresh workspace opens on.
 *
 * The copy is the reference still's (`docs/design/counterpart-firstrun-light.png`), less the
 * template clause: the still offers "Start with a template" and no template gallery exists.
 */
const server = setupServer(
  http.get("/api/v1/workspace", () =>
    HttpResponse.json({
      name: "Northwind Advisory",
      people: 6,
      agents: 3,
      mcp_url: "https://northwind.glosswork.app/mcp",
    }),
  ),
);

beforeAll(() => server.listen());
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});
afterAll(() => server.close());

describe("FirstRun", () => {
  it("asks the question, as the page's only h1", async () => {
    renderWithProviders(<FirstRun />);

    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("What do you want to keep track of?");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("sets the second clause in the agent colour, because it is addressed to an agent", async () => {
    // Sampling the reference stills returns exactly --color-agent-ink in both themes.
    renderWithProviders(<FirstRun />);

    const heading = await screen.findByRole("heading", { level: 1 });
    const accent = within(heading).getByText("keep track of?");
    expect(accent.className).toContain("text-agent-ink");
  });

  it("sets the question in the display face at the 8.5 size", async () => {
    renderWithProviders(<FirstRun />);

    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading.className).toContain("font-display");
    // --text-5xl is 36px, the size 8.5 names. Asserted as the token, not the number,
    // so the scale stays the single place the number lives.
    expect(heading.className).toContain("text-5xl");
  });

  it("prints the deployment's own MCP URL, from the server rather than the browser", async () => {
    renderWithProviders(<FirstRun />);

    expect(await screen.findByText("https://northwind.glosswork.app/mcp")).toBeInTheDocument();
  });

  it("falls back to the browser's origin when the deployment declared no base URL", async () => {
    server.use(
      http.get("/api/v1/workspace", () =>
        HttpResponse.json({ name: null, people: 1, agents: 0, mcp_url: null }),
      ),
    );
    renderWithProviders(<FirstRun />);

    expect(await screen.findByText(`${window.location.origin}/mcp`)).toBeInTheDocument();
  });

  it("offers the schema editor for building by hand, and no template gallery", async () => {
    renderWithProviders(<FirstRun />);

    const link = await screen.findByRole("link", { name: "set up a type by hand" });
    expect(link).toHaveAttribute("href", "/schema/new");
    expect(screen.queryByText(/template/i)).not.toBeInTheDocument();
  });

  it("explains what the prompt will do, in one paragraph", async () => {
    renderWithProviders(<FirstRun />);

    expect(
      await screen.findByText(
        "Nothing is set up yet, and that is the point. Paste this into the agent you already use. It will build the types and fields, explain each one, and offer to add your first records.",
      ),
    ).toBeInTheDocument();
  });

  it("renders nothing agent-flavoured that docs/DESIGN.md 5 forbids", async () => {
    const { container } = renderWithProviders(<FirstRun />);
    await screen.findByRole("heading", { level: 1 });

    const text = container.textContent ?? "";
    for (const forbidden of ["AI", "assistant", "automation", "smart", "magic", "!"]) {
      expect(text).not.toContain(forbidden);
    }
  });
});
