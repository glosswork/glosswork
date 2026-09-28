/**
 * Renders a `SearchHit.snippet` for `dangerouslySetInnerHTML` (docs/MCP_TOOLS.md 5.1). The server
 * highlights matched terms with literal `<em>`/`</em>` markers embedded in otherwise-untrusted
 * record or comment text, so the two steps must run in this order and never be collapsed into one:
 * escape every HTML metacharacter in the raw text first, and only then promote the now-escaped
 * marker sequences (`&lt;em&gt;` / `&lt;/em&gt;`) to real `<mark>`/`</mark>` tags. Escaping first
 * means hostile content such as `<script>alert(1)</script>` renders as inert text, never a real
 * element; a literal `<em>` typed into a record's own content is a cosmetic false highlight after
 * this, not an injection — accepted (docs/MCP_TOOLS.md 5.1).
 */

const HTML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => HTML_ESCAPES[char]);
}

export function renderSnippet(snippet: string): string {
  const escaped = escapeHtml(snippet);
  return escaped.replaceAll("&lt;em&gt;", "<mark>").replaceAll("&lt;/em&gt;", "</mark>");
}
