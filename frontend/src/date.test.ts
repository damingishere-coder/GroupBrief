import { describe, expect, it } from "vitest";

import { shanghaiDateInputValue } from "./date";

describe("Shanghai run date", () => {
  it("uses the Shanghai calendar day at a UTC boundary", () => {
    expect(shanghaiDateInputValue(new Date("2026-08-25T16:30:00.000Z"))).toBe("2026-08-26");
  });
});
