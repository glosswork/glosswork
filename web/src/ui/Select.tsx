/** Styled native `<select>`. Options pass through as children; every native
 * prop passes through untouched. */
import type { ComponentPropsWithoutRef } from "react";
import { selectClass } from "./classes";
import { cx } from "./cx";

export function Select({ className, ...rest }: ComponentPropsWithoutRef<"select">) {
  return <select className={cx(selectClass, className)} {...rest} />;
}
