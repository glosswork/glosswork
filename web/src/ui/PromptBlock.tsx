/**
 * The prompt block (`docs/DESIGN.md` 7.10): copyable text with a `Copy` button top-right.
 *
 * **The one idea in this component.** The block sets parts of its text in the mono face, so the DOM
 * is a handful of nodes while the clipboard takes one string. Rather than hold a rendered form and
 * a copyable form and hope they agree, it takes **one** string and splits it on the segments to
 * emphasise, so the rendered text is the copied text by construction. A segment that is not in the
 * text renders nothing and changes nothing.
 *
 * **It says so when it cannot copy.** The Clipboard API is gated on a secure context: it is
 * present on `http://localhost` and absent on `http://tracker.local:8000`, which is exactly the
 * plain-HTTP standalone deployment this screen is written for. No e2e run can observe
 * that, because the suite is served from localhost, so the fallback is asserted in jsdom and the
 * text stays selectable so the instruction the alert gives is one a person can follow.
 */
import { useState } from "react";
import { Button } from "./Button";

export interface PromptBlockProps {
  /** The whole text: rendered, and written to the clipboard, as one string. */
  text: string;
  /** Substrings of `text` to set in the mono face. Order does not matter; overlaps are not. */
  mono: readonly string[];
}

/**
 * Splits `text` on `mono`, keeping the separators, so joining the pieces returns `text` exactly.
 * Built by walking the string rather than with a regular expression, because the segments are
 * arbitrary text (a URL is full of regex metacharacters) and escaping them would be a second
 * thing to get wrong.
 */
function splitOnSegments(text: string, mono: readonly string[]): { part: string; isMono: boolean }[] {
  const pieces: { part: string; isMono: boolean }[] = [];
  let rest = text;
  while (rest.length > 0) {
    let bestIndex = -1;
    let bestSegment = "";
    for (const segment of mono) {
      if (segment.length === 0) continue;
      const index = rest.indexOf(segment);
      if (index !== -1 && (bestIndex === -1 || index < bestIndex)) {
        bestIndex = index;
        bestSegment = segment;
      }
    }
    if (bestIndex === -1) {
      pieces.push({ part: rest, isMono: false });
      break;
    }
    if (bestIndex > 0) {
      pieces.push({ part: rest.slice(0, bestIndex), isMono: false });
    }
    pieces.push({ part: bestSegment, isMono: true });
    rest = rest.slice(bestIndex + bestSegment.length);
  }
  return pieces;
}

export function PromptBlock({ text, mono }: PromptBlockProps) {
  const [copyFailed, setCopyFailed] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopyFailed(false);
    } catch {
      // Includes the case where there is no clipboard at all: reading `.writeText` off
      // `undefined` throws, and lands here rather than crashing the render.
      setCopyFailed(true);
    }
  }

  return (
    <div className="relative rounded-card border border-line-2 bg-surface p-5 pr-[76px] text-left">
      {/* `break-words` (overflow-wrap) on the paragraph rather than `break-all` on the mono
          segments: a long token then breaks only when it genuinely cannot fit, which is the URL
          at a narrow width. `break-all` broke `describe_capabilities` mid-identifier at 1280,
          which the reference still keeps whole -- found by looking at the rendered screen, not
          by reading the class. */}
      <p
        data-testid="prompt-block-text"
        className="text-base leading-relaxed break-words text-ink"
      >
        {splitOnSegments(text, mono).map((piece, index) =>
          piece.isMono ? (
            <span key={index} className="font-mono text-ink-2">
              {piece.part}
            </span>
          ) : (
            <span key={index}>{piece.part}</span>
          ),
        )}
      </p>
      <Button
        type="button"
        variant="secondary"
        className="absolute right-4 top-4"
        onClick={() => void handleCopy()}
      >
        Copy
      </Button>
      {copyFailed && (
        <p role="alert" className="mt-3 text-sm text-bad">
          Could not copy automatically. Select the text and copy it by hand.
        </p>
      )}
    </div>
  );
}
