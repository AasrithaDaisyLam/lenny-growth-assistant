import type { HealthResponse } from "../types";

/**
 * Readiness, per dependency.
 *
 * The failure reason lives in the `title` rather than being rendered inline: it
 * is long, but it is also the whole point of the component -- "degraded" alone
 * would leave the user guessing which dependency is down.
 */
export function HealthBadge({
	health,
	error,
}: {
	health: HealthResponse | null;
	error: string | null;
}) {
	if (error) {
		return (
			<span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-800 dark:bg-red-950 dark:text-red-200">
				API unreachable
			</span>
		);
	}
	if (!health) {
		return <span className="text-xs text-slate-400">checking…</span>;
	}

	const failed = Object.entries(health.components).filter(([, component]) => !component.ok);
	const healthy = health.status === "ok";
	const detail = failed.length
		? failed.map(([name, component]) => `${name}: ${component.error ?? "not ready"}`).join("\n")
		: "All dependencies healthy";

	return (
		<span
			title={detail}
			className={
				healthy
					? "rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
					: "rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900 dark:bg-amber-950 dark:text-amber-200"
			}
		>
			{healthy ? "healthy" : `degraded · ${failed.length}`}
		</span>
	);
}
