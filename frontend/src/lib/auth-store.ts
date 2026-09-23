"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Role } from "./types";

interface AuthState {
  token: string | null;
  email: string | null;
  role: Role | null;
  setSession: (token: string, email: string, role: Role) => void;
  clear: () => void;
}

// Persisted to localStorage so a refresh doesn't drop the session. The token itself is short-lived
// (8h, server-side) — see docs/THREAT_MODEL.md for what that trade-off means.
export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      email: null,
      role: null,
      setSession: (token, email, role) => set({ token, email, role }),
      clear: () => set({ token: null, email: null, role: null }),
    }),
    { name: "orbital-sentinel-auth" }
  )
);

const ROLE_RANK: Record<Role, number> = { viewer: 0, analyst: 1, admin: 2 };

export function hasRole(role: Role | null, minimum: Role): boolean {
  if (role === null) return false;
  return ROLE_RANK[role] >= ROLE_RANK[minimum];
}
