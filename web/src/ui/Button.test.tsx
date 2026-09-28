import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button } from "./Button";

describe("Button", () => {
  it("renders a real <button> with its accessible name and native props intact", async () => {
    const onClick = vi.fn();
    render(
      <Button type="button" variant="primary" onClick={onClick}>
        Save view
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Save view" });
    expect(button.tagName).toBe("BUTTON");
    expect(button).toHaveAttribute("type", "button");
    await userEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("honors disabled", async () => {
    const onClick = vi.fn();
    render(
      <Button type="button" disabled onClick={onClick}>
        Export CSV
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Export CSV" });
    expect(button).toBeDisabled();
    await userEvent.click(button).catch(() => undefined);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("appends caller classes to the variant's", () => {
    render(
      <Button type="button" variant="danger" className="extra-class">
        Delete 3 records
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Delete 3 records" });
    expect(button.className).toContain("text-bad");
    expect(button.className).toContain("extra-class");
  });
});
