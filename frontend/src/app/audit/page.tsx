"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { hasRole, useAuth } from "@/lib/auth-store";
import type { AuditLogOut, AuditVerifyOut } from "@/lib/types";

export default function AuditPage() {
  const { token, role } = useAuth();
  const [rows, setRows] = useState<AuditLogOut[] | null>(null);
  const [verify, setVerify] = useState<AuditVerifyOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const isAdmin = hasRole(role, "admin");

  useEffect(() => {
    if (!token || !isAdmin) return;
    let cancelled = false;
    api
      .auditLog(token)
      .then((p) => {
        if (!cancelled) setRows(p.items);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.detail : "could not load the audit log");
      });
    return () => {
      cancelled = true;
    };
  }, [token, isAdmin]);

  async function runVerify() {
    if (!token) return;
    setChecking(true);
    try {
      setVerify(await api.auditVerify(token));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "verification failed");
    } finally {
      setChecking(false);
    }
  }

  if (!isAdmin) {
    return (
      <p className="hud-panel rounded-lg border p-4 text-sm text-muted">
        The audit log is visible to admin accounts only. {token ? "Your role can't read it." : "Sign in first."}
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">Audit log</h1>
        <button
          onClick={runVerify}
          disabled={checking}
          className="ml-auto min-h-11 rounded bg-accent px-4 text-sm font-medium text-background hover:opacity-90 disabled:opacity-40"
        >
          {checking ? "Verifying…" : "Verify hash chain"}
        </button>
      </div>

      {verify && (
        <p role="status" className={`text-sm ${verify.ok ? "text-nominal" : "text-critical"}`}>
          {verify.ok
            ? "Chain intact: every entry's hash matches its predecessor."
            : `Chain broken at entry #${verify.first_bad_row}: that row was altered after it was written.`}
        </p>
      )}
      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="hud-panel overflow-x-auto rounded-lg border">
        <table className="w-full text-left text-xs">
          <caption className="sr-only">Most recent audit entries, newest first</caption>
          <thead className="text-muted">
            <tr className="border-b border-border">
              <th scope="col" className="p-2">#</th>
              <th scope="col" className="p-2">time</th>
              <th scope="col" className="p-2">actor</th>
              <th scope="col" className="p-2">action</th>
              <th scope="col" className="p-2">target</th>
            </tr>
          </thead>
          <tbody>
            {rows === null && (
              <tr><td colSpan={5} className="p-3 text-muted">Loading…</td></tr>
            )}
            {rows?.map((r) => (
              <tr key={r.id} className="border-b border-border last:border-0">
                <td className="mono p-2 text-muted">{r.id}</td>
                <td className="mono p-2">{new Date(r.ts).toLocaleString()}</td>
                <td className="p-2">{r.actor}</td>
                <td className="mono p-2 text-accent">{r.action}</td>
                <td className="mono max-w-48 truncate p-2 text-muted">{r.target}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
