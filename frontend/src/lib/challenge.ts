// Scoring for the "Attack or Accident?" challenge: guess a finished session's true class before
// revealing it, and keep a running tally. Pure functions plus a tiny localStorage wrapper so the
// logic is unit-testable without a browser.

export const GUESS_OPTIONS = [
  { value: "nominal", label: "Nothing wrong" },
  { value: "mechanical_failure", label: "Mechanical failure" },
  { value: "environmental", label: "Space weather" },
  { value: "sensor_malfunction", label: "Sensor malfunction" },
  { value: "cyberattack", label: "Cyberattack" },
] as const;

export type GuessValue = (typeof GUESS_OPTIONS)[number]["value"];

export interface Tally {
  played: number;
  correct: number;
  platformCorrect: number; // rounds where the platform's top verdict also matched the truth
  seen: string[]; // session ids already scored, so a refresh can't double-count
}

export const EMPTY_TALLY: Tally = { played: 0, correct: 0, platformCorrect: 0, seen: [] };

const KEY = "orbital-sentinel-challenge";

export function record(
  tally: Tally,
  sessionId: string,
  guess: string,
  truth: string,
  platformVerdict: string | null
): Tally {
  if (tally.seen.includes(sessionId)) return tally;
  return {
    played: tally.played + 1,
    correct: tally.correct + (guess === truth ? 1 : 0),
    platformCorrect: tally.platformCorrect + (platformVerdict === truth ? 1 : 0),
    seen: [...tally.seen, sessionId].slice(-200),
  };
}

export function accuracy(correct: number, played: number): number | null {
  return played === 0 ? null : correct / played;
}

export function loadTally(storage: Pick<Storage, "getItem"> | undefined): Tally {
  try {
    const raw = storage?.getItem(KEY);
    if (!raw) return EMPTY_TALLY;
    const t = JSON.parse(raw) as Partial<Tally>;
    return {
      played: Number(t.played) || 0,
      correct: Number(t.correct) || 0,
      platformCorrect: Number(t.platformCorrect) || 0,
      seen: Array.isArray(t.seen) ? t.seen.filter((s): s is string => typeof s === "string") : [],
    };
  } catch {
    return EMPTY_TALLY; // private mode / corrupt value: start fresh rather than crash
  }
}

export function saveTally(storage: Pick<Storage, "setItem"> | undefined, tally: Tally): void {
  try {
    storage?.setItem(KEY, JSON.stringify(tally));
  } catch {
    // storage unavailable or full: the tally just won't persist
  }
}
