import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuth } from "@/lib/auth-store";
import { Nav } from "./nav";

vi.mock("next/navigation", () => ({ usePathname: () => "/incidents" }));

beforeEach(() => useAuth.getState().clear());

describe("Nav", () => {
  it("offers sign in and no Audit link when signed out", () => {
    render(<Nav />);
    expect(screen.getByRole("link", { name: "sign in" })).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("link", { name: "Audit" })).not.toBeInTheDocument();
  });

  it("hides the Audit link from non-admins", () => {
    useAuth.getState().setSession("t", "analyst@example.test", "analyst");
    render(<Nav />);
    expect(screen.getByText(/analyst@example.test/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Audit" })).not.toBeInTheDocument();
  });

  it("shows the Audit link to admins and signs out on click", () => {
    useAuth.getState().setSession("t", "admin@example.test", "admin");
    render(<Nav />);
    expect(screen.getByRole("link", { name: "Audit" })).toHaveAttribute("href", "/audit");
    fireEvent.click(screen.getByRole("button", { name: "sign out" }));
    expect(useAuth.getState().token).toBeNull();
  });
});
