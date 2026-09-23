import { describe, expect, it } from "vitest";
import { hasRole } from "./auth-store";

describe("hasRole", () => {
  it("ranks viewer < analyst < admin", () => {
    expect(hasRole("viewer", "viewer")).toBe(true);
    expect(hasRole("viewer", "analyst")).toBe(false);
    expect(hasRole("viewer", "admin")).toBe(false);
    expect(hasRole("analyst", "viewer")).toBe(true);
    expect(hasRole("analyst", "analyst")).toBe(true);
    expect(hasRole("analyst", "admin")).toBe(false);
    expect(hasRole("admin", "admin")).toBe(true);
  });

  it("treats no session as no access, even to the lowest role", () => {
    expect(hasRole(null, "viewer")).toBe(false);
  });
});
