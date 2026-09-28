/**
 * The "About" control on the table page: a thin call site of `ui/GlossDisclosure.tsx`, which holds
 * every decision about the panel itself. What is left here is what is specific to this page: which
 * label it passes, which description it opens, and the reasoning tied to this screen rather than to
 * disclosures in general.
 *
 * `docs/DESIGN.md` 8.2: "The type's description no longer prints above the table; it is
 * available from an 'About' control beside the title, which opens the gloss panel." The panel
 * takes the shape 8.3 describes one screen over ("the field's gloss beneath it in a `sunk`
 * panel"): a disclosure. `docs/DESIGN.md` 7.11 carries that definition, and the record page
 * shares it through `GlossDisclosure`.
 *
 * **Why the description leaves the page flow at all.** Measured in the page flow, it took 36px of
 * the 454px above the first data row, on a screen where the data must lead. It is not deleted; it
 * is one click away and one `aria-expanded` away.
 *
 * **`heading-outline.spec.ts` walks this page's outline**, and the type's `h1` is its only
 * heading: `GlossDisclosure` emits none of its own (7.11), so this control does not add a second.
 *
 * **The test ids survive unchanged** (DD-41): e2e and unit tests already query
 * `data-testid="about-toggle"` and `"about-panel"`, so both are passed through explicitly rather
 * than left at `GlossDisclosure`'s own defaults.
 */
import { GlossDisclosure } from "../ui/GlossDisclosure";

export interface AboutDisclosureProps {
  /** The object type's description (AGENTS.md: descriptions are required and agent-facing). */
  description: string;
}

export function AboutDisclosure({ description }: AboutDisclosureProps) {
  return (
    <GlossDisclosure
      label="About"
      description={description}
      toggleTestId="about-toggle"
      panelTestId="about-panel"
    />
  );
}
