/** The theme choice control (docs/DESIGN.md 2.1). It lives in the shell, which is where the
 * design puts it. */
import { useState } from "react";

import { Select } from "./Select";
import { isThemeChoice, readThemeChoice, setThemeChoice, type ThemeChoice } from "./theme";

export function ThemeControl() {
  const [choice, setChoice] = useState<ThemeChoice>(() => readThemeChoice());

  return (
    <label className="flex items-center gap-3 text-sm text-ink">
      <span>Theme</span>
      <Select
        aria-label="Theme"
        value={choice}
        onChange={(event) => {
          const next = event.target.value;
          if (!isThemeChoice(next)) return;
          setChoice(next);
          setThemeChoice(next);
        }}
      >
        <option value="system">Match system</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </Select>
    </label>
  );
}
