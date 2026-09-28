/**
 * The copy `ReadOnlyBanner` renders. Its own module because `ReadOnlyBanner.tsx`
 * may export only components (`react-refresh/only-export-components`), and because a sentence a
 * test asserts character for character is worth being able to import on its own.
 */
import { levelAllows, type Level } from "../api/objectTypes";

/**
 * Three sentences, not one, because the schema editor has two thresholds rather than the one
 * every other gated screen has: proposing a schema change is `write` (proposing is not
 * deciding) while editing the object type or its fields is `admin`. A caller below `write`
 * there is missing controls at both levels, and a banner naming only one of them would explain
 * half of what it was hiding.
 */
export function readOnlyBannerMessage(
  typeName: string,
  level: Level,
  required: Exclude<Level, "none">,
): string {
  if (required !== "admin") {
    return (
      `Read-only. You hold ${level} on ${typeName}. ` +
      `Ask an administrator of ${typeName}, or a system administrator, for write access.`
    );
  }
  if (levelAllows(level, "write")) {
    return (
      `You hold ${level} on ${typeName}. Editing this object type and its fields needs ` +
      `admin on ${typeName}. Ask an administrator of ${typeName}, or a system ` +
      `administrator, for admin access.`
    );
  }
  return (
    `Read-only. You hold ${level} on ${typeName}. Proposing a schema change needs write on ` +
    `${typeName}, and editing this object type or its fields needs admin. Ask an ` +
    `administrator of ${typeName}, or a system administrator.`
  );
}

