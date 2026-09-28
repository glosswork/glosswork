/**
 * Renders user-authored markdown as React elements.
 *
 * SAFETY: nothing on this path parses raw HTML. `react-markdown` has no
 * string-returning API and no `dangerouslySetInnerHTML` on its path, and it does not parse raw
 * HTML *by default* — raw HTML becomes markup only if `rehype-raw` is added, which is
 * forbidden by name. So `<script>alert(1)</script>` in a comment renders as the characters a user
 * typed, and there is no HTML for a sanitizer to have to catch. The allowlist and `urlTransform`
 * below narrow further rather than being what makes this safe; do not remove them, and do not
 * mistake them for the guarantee.
 *
 * This is deliberately NOT the escape-then-promote shape of `search/renderSnippet.ts`. That file
 * builds an HTML string, so its two steps have an order that can be got wrong. This one never
 * builds a string. The habit carried over; the code did not.
 *
 * Heading APPEARANCE, not heading ELEMENTS. The app's pages keep an unbroken heading outline, and
 * the record detail page's is `h1` (the record key) then `h2` ("Comments"). A user writing
 * `###### note` in a comment would otherwise insert a real `<h6>` four levels below its parent,
 * skipping heading levels through content rather than through code. So every level is remapped
 * to a `<p class="md-h …">` carrying the DD-41 type scale: it looks like a heading and is not
 * one. The levels stay distinguishable by size.
 *
 * `whitespace-normal` is NOT redundant, and removing it breaks a shipped screen. `white-space`
 * inherits, and the table cell's pop-out is rendered from inside the `<td class="truncate">` it
 * opens from — `truncate` is `white-space: nowrap`. A `whitespace-pre-wrap` on that container
 * used to do double duty: it also overrode that inherited `nowrap`. With it gone and no
 * replacement the rendered value stopped wrapping, ran off the right edge of the dialog, and —
 * because a horizontally overflowing box is a scroll container, and Chrome makes scroll
 * containers keyboard-focusable — stole `showModal()`'s initial focus from the Edit button. Two
 * red Playwright scenarios, one inherited property. User content declares its own wrapping
 * rather than inheriting whatever the mount point happens to have.
 *
 * `remark-breaks` is why a single newline stays a visible line break. `whitespace-pre-wrap` at
 * three sites once kept line breaks through display; that class cannot stay here (it would turn
 * the literal `\n` text nodes react-markdown emits between block elements into a visible blank
 * line before every block), so the behavior moves to a real `<br>`
 * and the mechanism is different underneath it.
 */
import { createElement } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import { cx } from "./cx";

/**
 * The allowlist. `pre` AND `code`, not one of them: with `code` allowed and `pre` disallowed a
 * fenced block unwraps to a bare inline `<code>` and loses its block framing. `br` is here for
 * `remark-breaks`' output. `img` is absent so a user-authored string cannot beacon a remote host
 * from another reader's browser; `hr` is absent too.
 */
const ALLOWED_ELEMENTS = [
  "p",
  "br",
  "em",
  "strong",
  "code",
  "pre",
  "ul",
  "ol",
  "li",
  "blockquote",
  "a",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
];

const ALLOWED_SCHEMES = ["http:", "https:", "mailto:"];

/**
 * The link-scheme rule. The library's own `defaultUrlTransform` already resolves `javascript:`,
 * `data:` and `vbscript:` to `""`; this narrows to an allowlist instead, so a scheme nobody
 * thought of is refused rather than permitted. A relative URL is refused too: the rule names
 * three schemes, and a comment body is not a place to author app navigation.
 */
function restrictScheme(url: string): string {
  try {
    return ALLOWED_SCHEMES.includes(new URL(url).protocol) ? url : "";
  } catch {
    return "";
  }
}

/** DD-41 steps only: 16 / 14 / 13, never a library default. */
const HEADING_CLASS: Record<string, string> = {
  h1: "md-h text-lg font-semibold text-ink",
  h2: "md-h text-base font-semibold text-ink",
  h3: "md-h text-sm font-semibold text-ink",
  h4: "md-h text-sm font-semibold text-ink",
  h5: "md-h text-sm font-semibold text-ink-2",
  h6: "md-h text-sm font-semibold text-ink-2",
};

/**
 * One `components` override. Two things it must get right:
 *
 * 1. `react-markdown` passes its own hast node as a `node` prop, and `(props) => <a {...props} />`
 *    leaks it into the DOM, where it renders literally as `node="[object Object]"`. It is
 *    DELETED rather than destructured out: destructuring needs a binding, the binding is unused,
 *    and this project's eslint config carries no `ignoreRestSiblings`.
 * 2. `className` is applied last, so a class from the markdown source can never displace a
 *    design-system one (DD-41).
 */
function renderAs(tag: string, className: string, extra?: Record<string, string>) {
  return function MarkdownElement(props: { node?: unknown }) {
    const rest: Record<string, unknown> = { ...props };
    delete rest.node;
    return createElement(tag, { ...rest, ...extra, className });
  };
}

const COMPONENTS = {
  h1: renderAs("p", HEADING_CLASS.h1),
  h2: renderAs("p", HEADING_CLASS.h2),
  h3: renderAs("p", HEADING_CLASS.h3),
  h4: renderAs("p", HEADING_CLASS.h4),
  h5: renderAs("p", HEADING_CLASS.h5),
  h6: renderAs("p", HEADING_CLASS.h6),
  a: renderAs("a", "text-human-ink underline", { rel: "noopener noreferrer" }),
  ul: renderAs("ul", "list-disc space-y-0.5 pl-5"),
  ol: renderAs("ol", "list-decimal space-y-0.5 pl-5"),
  blockquote: renderAs("blockquote", "border-l-2 border-line-2 pl-3 text-ink-2"),
  code: renderAs("code", "rounded-ctl bg-ground px-1 font-mono"),
  // The inner `code` drops its own inline badge inside a fenced block: one background, not two.
  pre: renderAs(
    "pre",
    "overflow-x-auto rounded-card border border-line bg-ground p-2 font-mono "
      + "[&>code]:bg-transparent [&>code]:px-0",
  ),
};

export interface MarkdownProps {
  /** The user-authored string, exactly as stored. Never pre-processed before it gets here. */
  text: string;
  className?: string;
  "data-testid"?: string;
}

export function Markdown({ text, className, ...rest }: MarkdownProps) {
  return (
    <div {...rest} className={cx("whitespace-normal space-y-2 break-words", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkBreaks]}
        allowedElements={ALLOWED_ELEMENTS}
        // Keeps the TEXT of anything outside the allowlist rather than dropping it silently.
        // Text is inert; a value that quietly loses a sentence is not.
        unwrapDisallowed
        urlTransform={restrictScheme}
        components={COMPONENTS}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
