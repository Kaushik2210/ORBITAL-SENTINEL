"use client";

// Route-level error boundary: a render error in one page shows this instead of a blank screen, with
// a retry that re-renders just the failed segment. Deliberately generic — no stack trace or API
// detail is shown to the visitor.
export default function RouteError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div role="alert" className="hud-panel mx-auto max-w-md space-y-3 rounded-lg border p-6">
      <h1 className="text-lg font-semibold">Something went wrong</h1>
      <p className="text-sm text-muted">
        This page hit an unexpected error. Your session and data are unaffected.
      </p>
      <button
        onClick={reset}
        className="min-h-11 rounded bg-accent px-4 text-sm font-medium text-background hover:opacity-90"
      >
        Try again
      </button>
    </div>
  );
}
