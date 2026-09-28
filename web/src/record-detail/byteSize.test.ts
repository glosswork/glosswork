import { describe, expect, it } from "vitest";
import { formatByteSize } from "./byteSize";

describe("formatByteSize", () => {
  it("keeps bytes exact below a kibibyte", () => {
    expect(formatByteSize(0)).toBe("0 B");
    expect(formatByteSize(1023)).toBe("1023 B");
  });

  it("carries one decimal while the number is small enough to need it", () => {
    expect(formatByteSize(1024)).toBe("1.0 KB");
    expect(formatByteSize(1536)).toBe("1.5 KB");
  });

  it("drops the decimal once the number carries two digits of its own", () => {
    expect(formatByteSize(20480)).toBe("20 KB");
  });

  it("steps up through MB and GB", () => {
    expect(formatByteSize(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatByteSize(3 * 1024 * 1024 * 1024)).toBe("3.0 GB");
  });

  it("renders nothing for a size that is not one", () => {
    expect(formatByteSize(Number.NaN)).toBe("");
    expect(formatByteSize(-1)).toBe("");
  });
});
