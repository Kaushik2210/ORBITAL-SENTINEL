import { describe, expect, it } from "vitest";
import { accuracy, EMPTY_TALLY, loadTally, record, saveTally } from "./challenge";

describe("record", () => {
  it("counts a correct human guess and a matching platform verdict separately", () => {
    const t = record(EMPTY_TALLY, "s1", "cyberattack", "cyberattack", "needs_human");
    expect(t).toMatchObject({ played: 1, correct: 1, platformCorrect: 0 });
  });

  it("counts a wrong guess as played but not correct", () => {
    const t = record(EMPTY_TALLY, "s1", "nominal", "cyberattack", "cyberattack");
    expect(t).toMatchObject({ played: 1, correct: 0, platformCorrect: 1 });
  });

  it("never double-counts the same session, e.g. after a refresh", () => {
    const once = record(EMPTY_TALLY, "s1", "cyberattack", "cyberattack", null);
    const twice = record(once, "s1", "nominal", "cyberattack", null);
    expect(twice).toBe(once);
  });

  it("treats a missing platform verdict as not correct", () => {
    expect(record(EMPTY_TALLY, "s1", "nominal", "nominal", null).platformCorrect).toBe(0);
  });
});

describe("accuracy", () => {
  it("is null before any round, so the UI shows a dash instead of 0%", () => {
    expect(accuracy(0, 0)).toBeNull();
    expect(accuracy(3, 4)).toBe(0.75);
  });
});

describe("storage", () => {
  it("round-trips a tally", () => {
    const store = new Map<string, string>();
    const fake = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    const t = record(EMPTY_TALLY, "s1", "nominal", "nominal", "nominal");
    saveTally(fake, t);
    expect(loadTally(fake)).toEqual(t);
  });

  it("falls back to an empty tally on corrupt or unavailable storage", () => {
    expect(loadTally({ getItem: () => "{not json" })).toEqual(EMPTY_TALLY);
    expect(loadTally({ getItem: () => { throw new Error("denied"); } })).toEqual(EMPTY_TALLY);
    expect(loadTally(undefined)).toEqual(EMPTY_TALLY);
  });
});
