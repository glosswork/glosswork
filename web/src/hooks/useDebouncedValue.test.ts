/**
 * `useDebouncedValue`'s own test. Its one consumer is the relation picker's title search. This
 * file drives the
 * mechanism directly with fake timers rather than through that component, since the mechanism
 * is what every consumer, present or future, depends on.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useDebouncedValue } from "./useDebouncedValue";

afterEach(() => {
  vi.useRealTimers();
});

describe("useDebouncedValue", () => {
  it("returns the initial value immediately, with no timer advance", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useDebouncedValue("first", 200));

    expect(result.current).toBe("first");
  });

  it("still returns the old value the instant before delayMs has elapsed", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 200), {
      initialProps: { value: "first" },
    });

    rerender({ value: "second" });
    act(() => {
      vi.advanceTimersByTime(199);
    });

    expect(result.current).toBe("first");
  });

  it("returns the new value once delayMs has elapsed", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 200), {
      initialProps: { value: "first" },
    });

    rerender({ value: "second" });
    act(() => {
      vi.advanceTimersByTime(200);
    });

    expect(result.current).toBe("second");
  });

  it("resets the wait on every change, so a rapid run only ever shows the last value", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 200), {
      initialProps: { value: "first" },
    });

    rerender({ value: "second" });
    act(() => {
      vi.advanceTimersByTime(100);
    });
    rerender({ value: "third" });
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe("first");

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe("third");
  });

  it("honours an explicit delayMs rather than FILTER_DEBOUNCE_MS's default of 300", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 1000), {
      initialProps: { value: "first" },
    });

    rerender({ value: "second" });
    act(() => {
      vi.advanceTimersByTime(999);
    });
    expect(result.current).toBe("first");

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe("second");
  });

  it("leaves no pending timer once unmounted before delayMs has elapsed", () => {
    vi.useFakeTimers();
    const { unmount, rerender } = renderHook(({ value }) => useDebouncedValue(value, 200), {
      initialProps: { value: "first" },
    });

    rerender({ value: "second" });
    unmount();

    expect(vi.getTimerCount()).toBe(0);
  });
});
