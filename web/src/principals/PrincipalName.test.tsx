import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PrincipalName } from "./PrincipalName";
import type { PrincipalSidecar } from "../api/principals";

const SIDECAR: PrincipalSidecar = {
  "9f3c-a1": { display_name: "Sarah Okonjo", email: "sarah@example.com", is_active: true, type: "user" },
  "9f3c-a2": {
    display_name: "Nightly Importer",
    email: null,
    is_active: true,
    type: "service_account",
  },
};

describe("PrincipalName", () => {
  it("renders the resolved display name as plain text", () => {
    render(<PrincipalName id="9f3c-a1" principals={SIDECAR} />);
    expect(screen.getByText("Sarah Okonjo")).toBeInTheDocument();
    expect(document.querySelector(".font-mono")).toBeNull();
  });

  // What these two guard: an id the sidecar does not cover renders as a mono id rather than
  // disappearing (DD-25).
  it("renders the raw id in font-mono when it is not in the sidecar", () => {
    const { container } = render(<PrincipalName id="unknown-id" principals={SIDECAR} />);
    expect(container.textContent).toContain("unknown-id");
    expect(container.querySelector(".font-mono")?.textContent).toBe("unknown-id");
  });

  it("renders the raw id in font-mono when there is no sidecar at all", () => {
    const { container } = render(<PrincipalName id="unknown-id" />);
    expect(container.textContent).toContain("unknown-id");
    expect(container.querySelector(".font-mono")?.textContent).toBe("unknown-id");
  });

  it("renders a resolved principal through the primitive, with its kind as shape (6.1)", () => {
    // The wrong answer to guard against: a service account rendered exactly like a person.
    // `type` is on the sidecar so it cannot be.
    const { rerender } = render(<PrincipalName id="9f3c-a1" principals={SIDECAR} />);
    expect(screen.getByTestId("avatar").dataset.kind).toBe("person");
    rerender(<PrincipalName id="9f3c-a2" principals={SIDECAR} />);
    expect(screen.getByTestId("avatar").dataset.kind).toBe("agent");
  });

  it("renders the em dash for an absent value", () => {
    const { container } = render(<PrincipalName id={null} principals={SIDECAR} />);
    expect(container.textContent).toBe("—");
  });

  it("renders the em dash for an empty string", () => {
    const { container } = render(<PrincipalName id="" principals={SIDECAR} />);
    expect(container.textContent).toBe("—");
  });
});
