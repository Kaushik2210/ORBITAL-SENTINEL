import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PosteriorBars } from "./posterior-bars";

describe("PosteriorBars", () => {
  it("orders classes from most to least likely", () => {
    render(
      <PosteriorBars
        posterior={{ nominal: 0.1, cyberattack: 0.7, sensor_malfunction: 0.2 }}
      />
    );
    const labels = screen.getAllByText(/nominal|cyberattack|sensor_malfunction/).map((el) => el.textContent);
    expect(labels).toEqual(["cyberattack", "sensor_malfunction", "nominal"]);
  });

  it("renders each posterior as a percentage", () => {
    render(<PosteriorBars posterior={{ cyberattack: 0.993 }} />);
    expect(screen.getByText("99.3%")).toBeInTheDocument();
  });
});
