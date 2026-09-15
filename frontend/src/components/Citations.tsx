import type { Citation } from "../types";

export function CitationChip({
	citation,
	active,
	onSelect,
}: {
	citation: Citation;
	active: boolean;
	onSelect: (citation: Citation) => void;
}) {
	return (
		<button
			type="button"
			onClick={() => onSelect(citation)}
			title={`${citation.episode_title}${citation.speaker ? ` — ${citation.speaker}` : ""}`}
			className={
				active
					? "mx-0.5 rounded bg-sky-600 px-1.5 py-0.5 align-baseline text-[0.7rem] font-medium text-white"
					: "mx-0.5 rounded bg-sky-100 px-1.5 py-0.5 align-baseline text-[0.7rem] font-medium text-sky-800 hover:bg-sky-200 dark:bg-sky-950 dark:text-sky-200 dark:hover:bg-sky-900"
			}
		>
			{citation.marker}
		</button>
	);
}

/** The source behind a citation: this is what makes an answer checkable. */
export function SourcePanel({
	citations,
	selected,
	onSelect,
	onClose,
}: {
	citations: Citation[];
	selected: Citation | null;
	onSelect: (citation: Citation) => void;
	onClose: () => void;
}) {
	if (citations.length === 0) return null;

	return (
		<aside className="border-t border-slate-200 bg-slate-50 p-3 text-sm dark:border-slate-800 dark:bg-slate-900 md:w-96 md:shrink-0 md:border-l md:border-t-0">
			<div className="mb-2 flex items-center justify-between">
				<h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
					Sources ({citations.length})
				</h2>
				<button
					type="button"
					onClick={onClose}
					className="text-xs text-slate-500 hover:text-slate-900 dark:hover:text-slate-100"
				>
					close
				</button>
			</div>

			<ul className="space-y-1">
				{citations.map((citation) => (
					<li key={citation.marker}>
						<button
							type="button"
							onClick={() => onSelect(citation)}
							className={
								selected?.marker === citation.marker
									? "w-full rounded border border-sky-400 bg-white px-2 py-1 text-left dark:bg-slate-950"
									: "w-full rounded border border-transparent px-2 py-1 text-left hover:bg-white dark:hover:bg-slate-950"
							}
						>
							<span className="font-medium text-sky-700 dark:text-sky-300">
								{citation.marker}
							</span>{" "}
							<span className="text-slate-700 dark:text-slate-300">
								{citation.episode_title}
							</span>
							{citation.speaker ? (
								<span className="block text-xs text-slate-500">{citation.speaker}</span>
							) : null}
						</button>
					</li>
				))}
			</ul>

			{selected ? (
				<div className="mt-3 rounded border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-950">
					<div className="mb-1 text-xs text-slate-500">
						{selected.episode_slug} · chunk {selected.chunk_index}
					</div>
					<div className="font-medium text-slate-900 dark:text-slate-100">
						{selected.episode_title}
					</div>
					<div className="mt-1 text-xs text-slate-500">
						{selected.guest ?? "Unknown guest"}
						{selected.speaker ? ` · ${selected.speaker}` : ""}
					</div>
					{selected.youtube_url ? (
						<a
							href={selected.youtube_url}
							target="_blank"
							rel="noreferrer"
							className="mt-2 inline-block text-xs text-sky-700 underline dark:text-sky-300"
						>
							Open episode
						</a>
					) : null}
				</div>
			) : (
				<p className="mt-3 text-xs text-slate-500">
					Select a citation to see which episode and speaker it came from.
				</p>
			)}
		</aside>
	);
}
