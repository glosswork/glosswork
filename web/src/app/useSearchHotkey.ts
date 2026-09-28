/**
 * The `/` key opens search (docs/DESIGN.md 8.1).
 *
 * **The whole difficulty is knowing when `/` is a shortcut and when it is a character.** This
 * product is full of places a person types a slash on purpose: a filter value, a comment, a
 * field description, a URL in a text cell. A bare `keydown` listener navigates away mid-sentence
 * out of every one of them, and it does it silently, losing what was being typed. So the guard
 * is not an afterthought here, it is the feature; `e2e/shell.spec.ts` asserts both halves,
 * and the half that fails a naive implementation is asserted last.
 *
 * Also skipped: any modifier combination (`Ctrl+/` and `Cmd+/` belong to the browser and to
 * comment-toggle muscle memory), a repeat from a held key, and anything typed while a modal
 * `<dialog>` is open — the pop-out cell editor is a `<dialog>` with a textarea in it, and
 * navigating out from under an open modal loses an unsaved edit.
 */
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

/** True when the event's target is somewhere a slash is a character the user meant to type. */
function isTextEntry(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;

  const tag = target.tagName;
  // `<select>` is in here because typing a character in a native select jumps to the matching
  // option: `/` is a search key there too, just not this one.
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

export function useSearchHotkey(): void {
  const navigate = useNavigate();

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "/") return;
      if (event.repeat) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTextEntry(event.target)) return;
      if (document.querySelector("dialog[open]") !== null) return;

      // Prevented so the browser's own quick-find (Firefox's `/`) does not also open.
      event.preventDefault();
      navigate("/search");
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [navigate]);
}
