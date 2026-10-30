/**
 * A text box that keeps what was typed, beside a committed value it does not draw itself from.
 *
 * **Why this exists.** Two filter inputs commit something other than their own text: the comma
 * list commits an array, and the date box commits a canonical instant or nothing at all. Drawn
 * from the committed value, such a box rewrites itself on every key. The comma list lost its
 * comma that way (`v1, v2` typed key by key read `v1v2`, because `["v1"]` joins back to `v1`
 * before the next key lands), and a date box that commits `undefined` for an unfinished date
 * would empty itself on the first key and could never be typed into at all.
 *
 * So the text is this hook's own state, and the committed value is read back into it only when
 * it changes to something the typed text **does not already produce**: an operator change that
 * resets the value, or a saved view arriving with a different one. A change that is merely the
 * echo of what was just typed leaves the text alone.
 *
 * "Adjusting state when a prop changes", done during render as `TableView` does for its query
 * identity, rather than in an effect: an effect would paint the stale text once first.
 */
import { useState } from "react";

export interface TypedTextOptions<T> {
  /** The committed value, as the parent holds it. */
  committed: T;
  /** The text to show for a committed value that did not come from typing here. */
  textFor: (committed: T) => string;
  /** What `text` commits. Compared with `committed` to tell an echo from an outside change. */
  commitFor: (text: string) => T;
  /** Whether two committed values are the same value. */
  same: (a: T, b: T) => boolean;
  /**
   * True on a render where the committed value changed for a reason the caller will answer by
   * committing again from the same text, so the text must not be re-read from it. The date box
   * sets it when the operator changes: the parent resets the value, and the box re-derives it
   * from the day already typed.
   */
  hold?: boolean;
}

export function useTypedText<T>({
  committed,
  textFor,
  commitFor,
  same,
  hold = false,
}: TypedTextOptions<T>): [string, (text: string) => void] {
  const [text, setText] = useState(() => textFor(committed));
  const [seen, setSeen] = useState(committed);
  if (!same(committed, seen)) {
    setSeen(committed);
    if (!hold && !same(commitFor(text), committed)) {
      setText(textFor(committed));
    }
  }
  return [text, setText];
}
