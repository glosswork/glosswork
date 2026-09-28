import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Dialog } from "./Dialog";

describe("Dialog", () => {
  it("opens modally on mount and is queryable as role dialog by its label", () => {
    render(
      <Dialog label="Resolve version conflict" data-testid="merge-conflict-dialog">
        <p>content</p>
      </Dialog>,
    );
    const dialog = screen.getByRole("dialog", { name: "Resolve version conflict" });
    expect(dialog.tagName).toBe("DIALOG");
    expect(dialog).toHaveAttribute("open");
    expect(screen.getByTestId("merge-conflict-dialog")).toBe(dialog);
  });

  it("reports Escape through onCancel instead of closing itself", () => {
    const onCancel = vi.fn();
    render(
      <Dialog label="Resolve version conflict" onCancel={onCancel}>
        <p>content</p>
      </Dialog>,
    );
    const dialog = screen.getByRole("dialog");
    // jsdom never synthesizes the native cancel event from a keypress; fire it directly,
    // which is exactly what a real engine does on Escape inside showModal().
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(onCancel).toHaveBeenCalledOnce();
    // Prevented, so the dialog stays open until the parent unmounts it.
    expect(dialog).toHaveAttribute("open");
  });

  it("closes cleanly on unmount", () => {
    const { unmount } = render(
      <Dialog label="New access token">
        <p>content</p>
      </Dialog>,
    );
    expect(screen.getByRole("dialog")).toHaveAttribute("open");
    unmount();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
