/**
 * The `Status` cell shared by the People and Service accounts tables (docs/DESIGN.md 8.6).
 *
 * A word in `ink-2`, not a `Badge`. The old row-cards rendered `<Badge tone="neutral">
 * (inactive)</Badge>` and nothing at all for an active principal, which made "is this person
 * still here" a question you answered by noticing an absence. A column answers it for every row
 * at once, which is the whole argument for the table, and 7.3 reserves the pill for a select
 * value rather than for a fact the row already carries.
 *
 * `ink-2` and not `ink-3`: 2.1 measures `ink-3` on `ground` at 2.79:1 light and 4.00:1 dark
 * against its own 4.5:1 floor, and this is not placeholder or disabled text.
 */
export function PrincipalStatus({ isActive }: { isActive: boolean }) {
  return <span className="text-ink-2">{isActive ? "Active" : "Inactive"}</span>;
}
