import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SeverityBadge, SyntheticBadge, VerdictBadge } from "./badges";

describe("VerdictBadge", () => {
  it("labels cyberattack in plain English", () => {
    render(<VerdictBadge verdict="cyberattack" />);
    expect(screen.getByText("Cyberattack")).toBeInTheDocument();
  });

  it("labels needs_human distinctly from a real class", () => {
    render(<VerdictBadge verdict="needs_human" />);
    expect(screen.getByText("Needs human")).toBeInTheDocument();
  });
});

describe("SeverityBadge", () => {
  it("renders the raw severity string", () => {
    render(<SeverityBadge severity="critical" />);
    expect(screen.getByText("critical")).toBeInTheDocument();
  });
});

describe("SyntheticBadge", () => {
  it("distinguishes synthetic from real data in both label and title", () => {
    const { rerender } = render(<SyntheticBadge synthetic={true} />);
    expect(screen.getByText("synthetic")).toHaveAttribute("title", "Simulated data");

    rerender(<SyntheticBadge synthetic={false} />);
    expect(screen.getByText("real data")).toHaveAttribute("title", "Real measured data");
  });
});
