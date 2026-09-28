import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ApiError } from "../api/client";
import { Alert } from "./Alert";

describe("Alert", () => {
  it("renders the server's FR-A4 code and message from a caught ApiError (the error-envelope mechanism)", () => {
    const error = new ApiError(
      422,
      JSON.stringify({
        error: {
          code: "validation_failed",
          message: "Passwords must be at least 12 characters.",
          details: {},
        },
      }),
    );
    render(<Alert tone="error" title="Could not create the user." error={error} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Could not create the user.");
    expect(alert).toHaveTextContent("validation_failed");
    expect(alert).toHaveTextContent("Passwords must be at least 12 characters.");
  });

  it("falls back to the fixed string alone when the body is unparseable", () => {
    const error = new ApiError(500, "<html>Bad gateway</html>");
    render(<Alert tone="error" title="Could not mint the token." error={error} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Could not mint the token.");
    expect(alert.textContent).not.toContain("Bad gateway");
  });

  it("uses a polite status role for non-error tones", () => {
    render(<Alert tone="info" title="Search index catching up" />);
    expect(screen.getByRole("status")).toHaveTextContent("Search index catching up");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
