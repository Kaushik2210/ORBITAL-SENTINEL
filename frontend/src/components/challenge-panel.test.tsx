import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { ChallengePanel } from "./challenge-panel";

beforeEach(() => window.localStorage.clear());

function guess(label: string) {
  fireEvent.click(screen.getByLabelText(label));
  fireEvent.click(screen.getByRole("button", { name: "Lock in guess" }));
}

describe("ChallengePanel", () => {
  it("keeps Lock in guess disabled until an option is chosen", () => {
    render(<ChallengePanel sessionId="s1" truth="cyberattack" platformVerdict="cyberattack" />);
    expect(screen.getByRole("button", { name: "Lock in guess" })).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Cyberattack"));
    expect(screen.getByRole("button", { name: "Lock in guess" })).toBeEnabled();
  });

  it("scores a correct guess and persists the tally", () => {
    render(<ChallengePanel sessionId="s1" truth="cyberattack" platformVerdict="cyberattack" />);
    guess("Cyberattack");
    expect(screen.getByRole("status")).toHaveTextContent("Correct.");
    expect(screen.getByRole("status")).toHaveTextContent("also right");
    expect(screen.getByText(/1 played/)).toBeInTheDocument();
    expect(window.localStorage.getItem("orbital-sentinel-challenge")).toContain('"correct":1');
  });

  it("scores a wrong guess and reports a platform miss", () => {
    render(<ChallengePanel sessionId="s2" truth="cyberattack" platformVerdict="environmental" />);
    guess("Space weather");
    expect(screen.getByRole("status")).toHaveTextContent("Not this time.");
    expect(screen.getByRole("status")).toHaveTextContent("a miss");
  });

  it("shows needs human when the platform abstained", () => {
    render(<ChallengePanel sessionId="s3" truth="nominal" platformVerdict={null} />);
    guess("Nothing wrong");
    expect(screen.getByRole("status")).toHaveTextContent("needs human");
  });

  it("starts from a tally saved by an earlier session", () => {
    window.localStorage.setItem(
      "orbital-sentinel-challenge",
      JSON.stringify({ played: 4, correct: 3, platformCorrect: 2, seen: ["a", "b", "c", "d"] })
    );
    render(<ChallengePanel sessionId="s4" truth="nominal" platformVerdict="nominal" />);
    expect(screen.getByText(/you 75%/)).toBeInTheDocument();
    expect(screen.getByText(/4 played/)).toBeInTheDocument();
  });
});
