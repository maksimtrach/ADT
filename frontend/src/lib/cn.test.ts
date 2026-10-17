import { describe, expect, it } from "vitest";
import { cn } from "./cn";

describe("cn", () => {
  it("merges conflicting Tailwind utilities", () => {
    expect(cn("px-2 text-sm", "px-4")).toBe("text-sm px-4");
  });
});

