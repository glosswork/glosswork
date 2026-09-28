/**
 * The count line under the paged table.
 *
 * Split out from `TableView` so the arithmetic — which is the whole content of the line the
 * pager replaced the `truncated` notice with — is unit-testable without rendering.
 */

/** `Showing 201-400 of 512.`, or a plain count when there is nothing to page through. */
export function pageRangeLabel(
  pageIndex: number,
  pageSize: number,
  visibleCount: number,
  totalCount: number,
): string {
  if (visibleCount === 0) return `Showing 0 of ${totalCount}.`;
  const start = pageIndex * pageSize + 1;
  const end = start + visibleCount - 1;
  if (start === 1 && end === totalCount) return `Showing all ${totalCount}.`;
  return `Showing ${start}-${end} of ${totalCount}.`;
}
