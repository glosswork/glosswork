import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { FieldValue } from "./FieldValue";
import type { FieldDoc } from "../api/objectTypes";

function field(overrides: Partial<FieldDoc> & Pick<FieldDoc, "key" | "type">): FieldDoc {
  return {
    name: overrides.key,
    description: `The ${overrides.key} field.`,
    required: false,
    unique: false,
    indexed: false,
    embed: false,
    default: null,
    config: {},
    position: 0,
    operators: [],
    ...overrides,
  } as FieldDoc;
}

const MARKDOWN = "**bold** and\n- a list";

/**
 * `FieldValue` is where markdown's scope lives as one branch condition, so this is
 * where both sides of it are asserted.
 */
describe("FieldValue", () => {
  it("renders a long_text value as markdown", () => {
    render(<FieldValue field={field({ key: "notes", type: "long_text" })} value={MARKDOWN} />);
    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(document.querySelectorAll("li")).toHaveLength(1);
  });

  it("SCOPE FENCE: renders a short_text value as plain text", () => {
    // A FENCE, not a defect proof. Markdown excludes `short_text` deliberately: names and keys
    // live there, and a stray asterisk or underscore silently changing how one displays is the
    // outcome that decision exists to prevent. This guards behavior that does NOT change, so it
    // passes against the unfixed tree by construction and is not counted among the
    // failing-first assertions.
    const { container } = render(
      <FieldValue field={field({ key: "name", type: "short_text" })} value="**not bold**" />,
    );
    expect(container.textContent).toBe("**not bold**");
    expect(container.querySelector("strong")).toBeNull();
  });

  it("keeps the line-break treatment on the non-markdown branch", () => {
    // `whitespace-pre-wrap` lives here rather than at the three call sites. It is the mechanism
    // for every type that is not `long_text`; only the markdown path swaps it for `remark-breaks`.
    const { container } = render(
      <FieldValue field={field({ key: "name", type: "short_text" })} value={"a\nb"} />,
    );
    expect(container.querySelector("span")).toHaveClass("whitespace-pre-wrap");
  });

  it("still shows the em dash for an absent value, from formatFieldValue alone", () => {
    const { container } = render(
      <FieldValue field={field({ key: "notes", type: "long_text" })} value={null} />,
    );
    expect(container.textContent).toBe("—");
  });

  it("still resolves a single_select option label rather than the stored value", () => {
    const { container } = render(
      <FieldValue
        field={field({
          key: "status",
          type: "single_select",
          options: [{ value: "on_track", label: "On track", description: "Proceeding." }],
        })}
        value="on_track"
      />,
    );
    expect(container.textContent).toBe("On track");
  });

  it("renders a user_ref value's resolved display name via the principals sidecar", () => {
    const { container } = render(
      <FieldValue
        field={field({ key: "owner", type: "user_ref" })}
        value="9f3c-a1"
        principals={{ "9f3c-a1": { display_name: "Sarah Okonjo", email: "sarah@example.com", is_active: true, type: "user" } }}
      />,
    );
    // The value renders through the attribution primitive (docs/DESIGN.md 6), so the text
    // carries the avatar's initials too; the substance this guards -- the sidecar's display name
    // reaches the screen, not the id -- is what is asserted.
    expect(container.textContent).toContain("Sarah Okonjo");
    expect(container.textContent).not.toContain("9f3c-a1");
  });

  it("falls back to the raw id in font-mono when the sidecar has no entry for it", () => {
    const { container } = render(
      <FieldValue field={field({ key: "owner", type: "user_ref" })} value="unresolved-id" />,
    );
    // On the same terms as the sibling above. DD-25's fallback: an id the sidecar does not
    // cover renders in mono rather than disappearing.
    expect(container.textContent).toContain("unresolved-id");
    expect(container.querySelector(".font-mono")?.textContent).toBe("unresolved-id");
  });
});
