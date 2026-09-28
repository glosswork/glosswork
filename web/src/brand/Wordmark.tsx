/**
 * The lockup: the tile followed by the wordmark (docs/DESIGN.md 4.2, 4.3).
 *
 * The bars are never redrawn in a component; the SVG is imported (web/src/brand/README.md).
 * 4.2's rules are encoded here rather than repeated at each call site: display face, weight
 * 600, tracked -0.03em at or above 24px, sentence case, tile at cap height with a gap of
 * 0.4em, and never the wordmark without the tile above 20px.
 */
import tileUrl from "./mark-tile.svg";

export function Wordmark({ size = 40 }: { size?: number }) {
  return (
    <span className="inline-flex items-center" style={{ gap: "0.4em" }}>
      <img src={tileUrl} alt="" width={size} height={size} className="rounded-frame" />
      <span
        className="font-display font-semibold text-ink"
        style={{ fontSize: size * 0.6, letterSpacing: "-0.03em" }}
      >
        Glosswork
      </span>
    </span>
  );
}
