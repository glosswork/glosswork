/**
 * The trial banner in each of its states. What the states *are* is `trialCountdown.test.ts`;
 * where the strip sits is `e2e/trial-banner.spec.ts`, because layout is never proven in jsdom.
 *
 * The words come from `TRIAL_COPY`, so a wording change is not a change to this file.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";

import { TrialBanner } from "./TrialBanner";
import { TRIAL_COPY } from "./trialCountdown";

const ENDS_AT = "2030-01-02T00:00:00Z";
const SUBSCRIBE_URL = "https://subscribe.example.com/plan";
const ONE_MINUTE_IN = new Date("2030-01-01T00:01:00Z");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(ONE_MINUTE_IN);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("TrialBanner", () => {
  it("shows the time left and a subscribe link while the trial is running", () => {
    render(<TrialBanner trial={{ ends_at: ENDS_AT, subscribe_url: SUBSCRIBE_URL }} />);

    expect(screen.getByTestId("trial-time-left")).toHaveTextContent(/^23:59$/);
    expect(screen.getByTestId("trial-message").textContent).toBe(
      `${TRIAL_COPY.runningLead} 23:59 ${TRIAL_COPY.runningTail}`,
    );
    const link = screen.getByTestId("trial-subscribe");
    expect(link).toHaveTextContent(TRIAL_COPY.subscribe);
    expect(link).toHaveAttribute("href", SUBSCRIBE_URL);
    // The same tab.
    expect(link).not.toHaveAttribute("target");
  });

  it("gives a screen reader the time in words and hides the digits from it", () => {
    render(<TrialBanner trial={{ ends_at: ENDS_AT, subscribe_url: SUBSCRIBE_URL }} />);

    expect(screen.getByTestId("trial-message-spoken").textContent).toBe(
      `${TRIAL_COPY.runningLead} 23 hours 59 minutes left.`,
    );
    expect(screen.getByTestId("trial-message")).toHaveAttribute("aria-hidden", "true");
  });

  it("is a labelled region and not a live region", () => {
    render(<TrialBanner trial={{ ends_at: ENDS_AT, subscribe_url: SUBSCRIBE_URL }} />);

    const region = screen.getByRole("region", { name: TRIAL_COPY.regionLabel });
    expect(region).toBe(screen.getByTestId("trial-banner"));
    // A live region would interrupt a screen reader every minute.
    expect(region.closest("[aria-live]")).toBeNull();
    expect(region.querySelector("[aria-live], [role='status'], [role='alert'], [role='timer']"))
      .toBeNull();
  });

  it("shows the time left with no link when there is no subscribe address", () => {
    render(<TrialBanner trial={{ ends_at: ENDS_AT, subscribe_url: null }} />);

    expect(screen.getByTestId("trial-time-left")).toHaveTextContent(/^23:59$/);
    expect(screen.queryByTestId("trial-subscribe")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("says the trial ended, with the same link and no time, once the end time has passed", () => {
    vi.setSystemTime(new Date(ENDS_AT));
    render(<TrialBanner trial={{ ends_at: ENDS_AT, subscribe_url: SUBSCRIBE_URL }} />);

    expect(screen.getByTestId("trial-message").textContent).toBe(TRIAL_COPY.ended);
    expect(screen.queryByTestId("trial-time-left")).toBeNull();
    expect(screen.queryByTestId("trial-message-spoken")).toBeNull();
    const link = screen.getByTestId("trial-subscribe");
    expect(link).toHaveTextContent(TRIAL_COPY.subscribe);
    expect(link).toHaveAttribute("href", SUBSCRIBE_URL);
  });

  it("says the trial ended with no link when there is no subscribe address", () => {
    vi.setSystemTime(new Date(ENDS_AT));
    render(<TrialBanner trial={{ ends_at: ENDS_AT, subscribe_url: null }} />);

    expect(screen.getByTestId("trial-message").textContent).toBe(TRIAL_COPY.ended);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("follows the clock by itself, through the end of the trial", () => {
    render(<TrialBanner trial={{ ends_at: ENDS_AT, subscribe_url: SUBSCRIBE_URL }} />);
    expect(screen.getByTestId("trial-time-left")).toHaveTextContent(/^23:59$/);

    // Advancing the timers advances the mocked clock with them.
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByTestId("trial-time-left")).toHaveTextContent(/^23:58$/);

    act(() => {
      vi.setSystemTime(new Date(ENDS_AT));
      vi.advanceTimersByTime(1_000);
    });
    expect(screen.getByTestId("trial-message").textContent).toBe(TRIAL_COPY.ended);
  });

  it("renders nothing for an end time the browser cannot parse", () => {
    const { container } = render(
      <TrialBanner trial={{ ends_at: "not-a-time", subscribe_url: SUBSCRIBE_URL }} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("leaves no timer running once it is unmounted", () => {
    const { unmount } = render(
      <TrialBanner trial={{ ends_at: ENDS_AT, subscribe_url: SUBSCRIBE_URL }} />,
    );
    expect(vi.getTimerCount()).toBe(1);

    unmount();

    expect(vi.getTimerCount()).toBe(0);
  });
});
