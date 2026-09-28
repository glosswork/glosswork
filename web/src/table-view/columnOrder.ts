/** Pure column-reorder helper behind the column picker's move-up/move-down buttons (the
 * reorder affordance; drag-and-drop is not required). */
export function moveColumn(order: string[], key: string, direction: "up" | "down"): string[] {
  const index = order.indexOf(key);
  if (index === -1) return order;
  const swapWith = direction === "up" ? index - 1 : index + 1;
  if (swapWith < 0 || swapWith >= order.length) return order;
  const next = [...order];
  [next[index], next[swapWith]] = [next[swapWith], next[index]];
  return next;
}
