import { pct, price, rate, ratio, signClass } from "./format";

describe("format helpers", () => {
  it("formats prices by magnitude", () => {
    expect(price(90000)).toBe("90,000.00");
    expect(price(1.23456)).toBe("1.2346");
    expect(price(0.000123456)).toBe("0.000123456");
    expect(price(null)).toBe("—");
  });
  it("formats percentages with sign so polarity is never colour-only", () => {
    expect(pct(4)).toBe("+4.00%");
    expect(pct(-1.5)).toBe("-1.50%");
    expect(pct(null)).toBe("—");
  });
  it("handles ratios, rates and infinity", () => {
    expect(ratio("inf")).toBe("∞");
    expect(ratio(1.234)).toBe("1.23");
    expect(rate(0.4)).toBe("40.0%");
    expect(signClass(2)).toBe("text-up");
    expect(signClass(-2)).toBe("text-down");
  });
});
