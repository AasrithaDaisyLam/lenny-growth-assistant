import type { ProvidersResponse, Session } from "../types";

/**
 * The active-provider control.
 *
 * Unavailable providers stay in the list, disabled and labelled with the reason
 * ("ANTHROPIC_API_KEY is not set"). Removing them would hide the capability and
 * make a configuration problem look like a missing feature.
 */
export function ProviderControl({
	providers,
	session,
	onChange,
	disabled,
}: {
	providers: ProvidersResponse | null;
	session: Session | null;
	onChange: (provider: string) => void;
	disabled: boolean;
}) {
	if (!providers) return null;
	const current = session?.provider ?? providers.active;

	return (
		<label className="flex min-w-0 items-center gap-2 text-xs">
			<span className="shrink-0 text-slate-500">Model</span>
			<select
				value={current}
				disabled={disabled || !session}
				onChange={(event) => onChange(event.target.value)}
				title={session ? `Active model: ${session.model}` : "No session selected"}
				className="min-w-0 max-w-[11rem] truncate rounded-md border border-slate-300 bg-white px-2 py-1 text-xs disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900"
			>
				{providers.providers.map((provider) => (
					<option key={provider.name} value={provider.name} disabled={!provider.available}>
						{provider.name}
						{provider.available ? "" : ` — ${provider.reason ?? "unavailable"}`}
					</option>
				))}
			</select>
			{session ? (
				<span className="hidden truncate text-slate-400 sm:inline">{session.model}</span>
			) : null}
		</label>
	);
}
