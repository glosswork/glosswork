import { describe, expect, it } from "vitest";
import { renderSnippet } from "./renderSnippet";

describe("renderSnippet", () => {
  it("promotes the server's <em> markers to <mark>", () => {
    expect(renderSnippet("pushed back on the <em>sales pricing</em> exception")).toBe(
      "pushed back on the <mark>sales pricing</mark> exception",
    );
  });

  it("escapes hostile content instead of rendering it as an element", () => {
    const rendered = renderSnippet("before <script>alert(1)</script> after");
    expect(rendered).not.toContain("<script>");
    expect(rendered).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
  });

  it("escapes the other HTML metacharacters", () => {
    expect(renderSnippet(`a & b "quoted" 'single' <tag>`)).toBe(
      "a &amp; b &quot;quoted&quot; &#39;single&#39; &lt;tag&gt;",
    );
  });

  it("promotes multiple <em> pairs in one snippet", () => {
    expect(renderSnippet("<em>a</em> and <em>b</em>")).toBe("<mark>a</mark> and <mark>b</mark>");
  });

  it("leaves plain text with no markers unchanged apart from escaping", () => {
    expect(renderSnippet("no markers here")).toBe("no markers here");
  });
});
