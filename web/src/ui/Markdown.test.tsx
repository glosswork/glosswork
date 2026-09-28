import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Markdown } from "./Markdown";

/**
 * The markdown renderer's contract.
 *
 * The first block is the safety rule: nothing on this path parses raw HTML. It was written before
 * the component existed and observed failing, which is the only way to show that a test of code
 * that does not exist yet can fail.
 *
 * Note what this file does NOT inherit from `search/renderSnippet.ts`: its escape-then-promote
 * ordering. That ordering exists because `renderSnippet` produces an
 * HTML *string* for `dangerouslySetInnerHTML`. `react-markdown` produces React elements and never
 * an HTML string, so there is nothing to escape and no ordering to get wrong — the habit carries
 * over, the code does not.
 */
describe("Markdown: raw HTML never becomes markup", () => {
  const HOSTILE = '<script>alert(1)</script>\n\n<img src=x onerror="alert(2)">';

  it("renders a script tag and an onerror attribute as literal text, not as elements", () => {
    const { container } = render(<Markdown text={HOSTILE} data-testid="md" />);

    // The proof is two-sided: the characters are visible to the reader...
    expect(screen.getByTestId("md").textContent).toContain("<script>alert(1)</script>");
    expect(screen.getByTestId("md").textContent).toContain('onerror="alert(2)"');
    // ...and no element was created from them. `querySelector("script")` is the assertion that
    // actually matters; the text one alone would pass on a page that ALSO executed the tag.
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    // The `onerror=` characters DO appear in `innerHTML` — as the text of an escaped `&lt;img`,
    // which is the point. What must not exist is an element carrying it as an attribute.
    expect(container.querySelectorAll("[onerror]")).toHaveLength(0);
    expect(container.innerHTML).toContain("&lt;img");
    expect(container.innerHTML).toContain("&lt;script&gt;");
  });

  it("renders no image element for markdown image syntax (no remote beacon)", () => {
    const { container } = render(<Markdown text="![beacon](https://example.invalid/pixel.png)" />);
    expect(container.querySelector("img")).toBeNull();
  });

  it("neutralizes a javascript: URL and keeps http, https and mailto", () => {
    const { container } = render(
      <Markdown
        text={
          "[bad](javascript:alert(3)) [plain](http://example.invalid/a) "
          + "[secure](https://example.invalid/b) [mail](mailto:someone@example.invalid)"
        }
      />,
    );
    const hrefs = Array.from(container.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(hrefs).not.toContain("javascript:alert(3)");
    expect(hrefs).toContain("http://example.invalid/a");
    expect(hrefs).toContain("https://example.invalid/b");
    expect(hrefs).toContain("mailto:someone@example.invalid");
  });

  it("carries rel=noopener noreferrer on every rendered link", () => {
    const { container } = render(<Markdown text="[a](https://example.invalid/a)" />);
    const link = container.querySelector("a");
    expect(link).not.toBeNull();
    expect(link!.getAttribute("rel")).toBe("noopener noreferrer");
    // Finding: a `components` override that spreads its props leaks react-markdown's own `node`
    // prop into the DOM as `node="[object Object]"`. Destructured out; asserted so it stays out.
    expect(link!.getAttribute("node")).toBeNull();
  });
});

describe("Markdown: heading appearance without heading elements", () => {
  it("emits no h1-h6 element for any user-authored heading level", () => {
    const { container } = render(
      <Markdown text={"# one\n\n## two\n\n### three\n\n#### four\n\n##### five\n\n###### six"} />,
    );
    expect(container.querySelectorAll("h1, h2, h3, h4, h5, h6")).toHaveLength(0);
    // The text survives — the heading is rendered, not dropped.
    expect(container.textContent).toContain("one");
    expect(container.textContent).toContain("six");
  });

  it("gives each level heading appearance from the DD-41 type scale", () => {
    const { container } = render(<Markdown text={"# one\n\n## two\n\n### three"} />);
    const headings = Array.from(container.querySelectorAll(".md-h"));
    expect(headings).toHaveLength(3);
    // Every size is a DD-41 step, never a library default, and the levels stay
    // distinguishable rather than collapsing to one weight.
    expect(headings.map((h) => h.className)).toEqual([
      expect.stringContaining("text-lg"),
      expect.stringContaining("text-base"),
      expect.stringContaining("text-sm"),
    ]);
    for (const heading of headings) {
      expect(heading.className).toContain("font-semibold");
    }
  });
});

describe("Markdown: a single newline stays a visible line break", () => {
  it("renders a soft break as a <br>, not as a collapsed space", () => {
    const { container } = render(<Markdown text={"line one\nline two"} />);
    // CommonMark alone collapses this to one line. `remark-breaks` is why it does not, and this
    // is the assertion that fails if the plugin is ever dropped.
    expect(container.querySelectorAll("br")).toHaveLength(1);
    expect(container.querySelectorAll("p")).toHaveLength(1);
  });

  it("still renders a blank line as a paragraph break", () => {
    const { container } = render(<Markdown text={"para one\n\npara two"} />);
    expect(container.querySelectorAll("p")).toHaveLength(2);
  });
});

describe("Markdown: the structure a real comment body was written to produce", () => {
  it("renders the observed comment body as a list and a heading, not as syntax", () => {
    // The literal body observed in the audit trail.
    const { container } = render(<Markdown text={"Another comment\n\n- with\n- markdown\n\n## title"} />);
    expect(container.querySelectorAll("ul")).toHaveLength(1);
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector(".md-h")?.textContent).toBe("title");
    // The syntax characters are gone from the reader's view, which is the whole complaint.
    expect(container.textContent).not.toContain("## title");
    expect(container.textContent).not.toContain("- with");
  });

  it("keeps a fenced block inside a <pre>, not as a bare <code>", () => {
    // The allowlist needs BOTH `pre` and `code`: with `code` allowed and `pre` not, a fenced
    // block unwraps to a bare inline `<code>` and loses its block framing (found by trying it).
    const { container } = render(<Markdown text={"```\nuv run pytest -q\n```"} />);
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre!.querySelector("code")).not.toBeNull();
  });

  it("renders inline code, emphasis, strong and blockquote", () => {
    const { container } = render(
      <Markdown text={"*em* **strong** `code`\n\n> quoted"} />,
    );
    expect(container.querySelector("em")).not.toBeNull();
    expect(container.querySelector("strong")).not.toBeNull();
    expect(container.querySelector("code")).not.toBeNull();
    expect(container.querySelector("blockquote")).not.toBeNull();
  });

  it("renders GFM syntax as literal text: remark-gfm is deliberately absent", () => {
    // Not a defect, recorded here rather than left to be discovered: remark-breaks is one
    // plugin, not a policy of adding plugins. If `remark-gfm` is ever added, this test is
    // the one that says so out loud.
    const { container } = render(<Markdown text={"| a | b |\n| --- | --- |\n| 1 | 2 |"} />);
    expect(container.querySelector("table")).toBeNull();
    expect(container.textContent).toContain("|");
  });

  it("renders prose with no markdown in it unchanged", () => {
    const { container } = render(<Markdown text="Just an ordinary sentence." />);
    expect(container.textContent).toBe("Just an ordinary sentence.");
  });
});
