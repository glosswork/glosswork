import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import { Blob as NodeBlob, File as NodeFile } from "node:buffer";
import { FormData as UndiciFormData } from "undici";

// vitest.config.ts sets `globals: false`, so @testing-library/react's own auto-cleanup (which
// only registers when it finds a global `afterEach`) never fires. Register it explicitly so
// each test starts from an empty DOM instead of accumulating previous tests' render trees.
afterEach(() => {
  cleanup();
});

/**
 * Vitest's `jsdom` test environment installs jsdom's own `Blob`/`File`/`FormData` classes as
 * ambient globals (needed for jsdom-faithful `<input type="file">`/`FileList` behavior), but
 * Node's built-in `fetch` (undici, used by both the app's `fetch` calls and MSW's `msw/node`
 * interceptor) only recognizes its own `Blob`/`File`/`FormData` implementations. A `FormData`
 * built from a jsdom `Blob` silently serializes as the literal string `"undefined"` with
 * filename `"blob"` once it crosses into `fetch` — a real interop gap between the two libraries,
 * not an application bug (reproduced directly: `jsdomBlob instanceof undiciBlob` is `false`, and
 * jsdom's `FormData.set` rejects an undici `Blob` outright with the reverse pairing). Overriding
 * the ambient globals with `node:buffer`'s `Blob`/`File` (Node's own fetch-compatible
 * implementation) and `undici`'s `FormData` fixes both directions: `userEvent.upload` still works
 * (it only needs a `Blob`-shaped object with `.text()`/`.arrayBuffer()`), and a real multipart
 * body built from the uploaded content round-trips correctly through `fetch` to an MSW handler's
 * `request.formData()`. Test-environment-only; production code is unaffected (browsers do not
 * have this split).
 */
globalThis.Blob = NodeBlob as unknown as typeof Blob;
globalThis.File = NodeFile as unknown as typeof File;
globalThis.FormData = UndiciFormData as unknown as typeof FormData;

/**
 * jsdom 30.0.1 parses `<dialog>` but does not implement its methods — `showModal()` and
 * `close()` are undefined (verified 2026-08-26 by executing them; DD-41) — so the
 * `ui/Dialog` primitive would crash on mount under vitest. Minimal shim: open-state
 * bookkeeping via the reflected `open` attribute, `returnValue`, and the `close` event. No
 * focus containment — only a real engine provides that, and Playwright covers it against the
 * built app.
 */
if (
  typeof HTMLDialogElement !== "undefined" &&
  typeof HTMLDialogElement.prototype.showModal !== "function"
) {
  HTMLDialogElement.prototype.show = function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function (this: HTMLDialogElement, returnValue?: string) {
    if (returnValue !== undefined) {
      this.returnValue = returnValue;
    }
    this.removeAttribute("open");
    this.dispatchEvent(new Event("close"));
  };
}
