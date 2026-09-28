import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge } from "./Badge";
import { EmptyState } from "./EmptyState";
import { Spinner } from "./Spinner";

describe("Badge", () => {
  it("renders an inline span with the tone's classes", () => {
    render(<Badge tone="danger">At risk</Badge>);
    const badge = screen.getByText("At risk");
    expect(badge.tagName).toBe("SPAN");
    expect(badge.className).toContain("text-bad");
  });
});

describe("Spinner", () => {
  it("announces politely and defaults to the literal the screens already render", () => {
    render(<Spinner />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading...");
  });

  it("takes a screen-specific label", () => {
    render(<Spinner label="Loading audit events..." />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading audit events...");
  });
});

describe("EmptyState", () => {
  it("renders title, body, and the action", () => {
    render(
      <EmptyState
        title="No records match this filter"
        action={<button type="button">Clear filter</button>}
        data-testid="table-empty"
      >
        Owner is Finance and Status is At risk matched nothing.
      </EmptyState>,
    );
    expect(screen.getByTestId("table-empty")).toHaveTextContent("No records match this filter");
    expect(screen.getByRole("button", { name: "Clear filter" })).toBeInTheDocument();
  });
});
