/**
 * docs/DESIGN.md 2.4's density presets: comfortable rows by default, compact by choice.
 *
 * **The records table only.** The other five tables sharing `tableClasses.ts` take the
 * comfortable default and never toggle, because only this one has a toolbar and a saved view to
 * persist a choice into.
 *
 * **A chip, not a visible pair of buttons.** The case for the pair is real: "Two mutually
 * exclusive presets read better as a visible pair than as a sentence about whichever one is
 * chosen, which is the same instinct DD-42 has about hiding the option you did not pick." And
 * 7.4 enumerates exactly two chip forms — group and sort — and does not name this one.
 *
 * What decides it is a measurement rather than a second opinion. 8.2 asks for a **one-row
 * toolbar** and groups "group, sort, columns and density controls" in one breath, and the
 * browser suite asserts that row at 1280 **and at 800**. The visible pair was 197.8px wide —
 * wider than any other control in the row, and 96px wider than this chip — and once the fixture
 * carried a saved view whose name the View menu's trigger has to show, the pair put the toolbar
 * into two rows at 800:
 *
 *     toolbar wrapped at 800px:
 *       4 "ComfortableCompact" y=119.53125
 *       6 "Active cutovers▾"   y=153.640625     Expected: <= 1   Received: 34.21875
 *
 * So density joins the chip grammar.
 *
 * **The chip says the preset, not the noun.** `Density: Comfortable` measures 163px against this
 * form's 103px, which does not fit the one-row toolbar. 7.4 supplies no
 * sentence for this control, any more than it does for the column picker, so the word is a
 * choice; the noun is one click away, on the popover's own `role="group"` label and in the
 * chip's accessible name.
 *
 * **The preset buttons keep their names and test ids inside the popover**, and that matters here:
 * `e2e/density.spec.ts` drives `density-comfortable` and `density-compact` to measure 2.4's real
 * row heights at both presets, and that spec is about row geometry rather than about the chip. It
 * needs one opening click per preset, and nothing else.
 */
import { Button } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { btnSmClass } from "../ui/classes";
import { cx } from "../ui/cx";
import type { Density } from "../ui/density";

export interface DensityToggleProps {
  density: Density;
  onChange: (density: Density) => void;
}

const OPTIONS: { value: Density; label: string }[] = [
  { value: "comfortable", label: "Comfortable" },
  { value: "compact", label: "Compact" },
];

export function DensityToggle({ density, onChange }: DensityToggleProps) {
  const current = OPTIONS.find((option) => option.value === density) ?? OPTIONS[0];

  return (
    <Chip
      data-testid="density-chip"
      // The preset alone does not say what it is a preset of, so the noun rides the accessible
      // name and the visible word stays inside it (docs/DESIGN.md 10: every interactive element
      // has a name; WCAG 2.5.3: the visible label is part of it).
      label={`Row density: ${current.label}`}
      popover={
        <div
          className="flex flex-col items-stretch gap-1"
          role="group"
          aria-label="Row density"
          data-testid="density-toggle"
        >
          {OPTIONS.map((option) => {
            const selected = density === option.value;
            return (
              <Button
                key={option.value}
                type="button"
                variant="quiet"
                // `aria-pressed` rather than a `<select>`: two mutually exclusive presets read
                // better as a pair of toggles, and inside the popover both are still visible —
                // which is the half of 6b's argument that survives the move. The option you did
                // not pick is shown rather than folded into a sentence about the one you did.
                aria-pressed={selected}
                data-testid={`density-${option.value}`}
                className={cx(btnSmClass, selected && "bg-human-soft font-medium text-human-ink")}
                onClick={() => onChange(option.value)}
              >
                {option.label}
              </Button>
            );
          })}
        </div>
      }
    >
      {current.label}
    </Chip>
  );
}
