import Link from "next/link";

export default function NotFound() {
  return (
    <div className="hud-panel mx-auto max-w-md space-y-3 rounded-lg border p-6">
      <h1 className="text-lg font-semibold">Page not found</h1>
      <p className="text-sm text-muted">That address doesn&apos;t exist in Mission Control.</p>
      <Link href="/" className="inline-flex min-h-11 items-center text-accent hover:underline">
        Back to the dashboard
      </Link>
    </div>
  );
}
