import type { Session } from "../types";

export function SessionSidebar({
	sessions,
	activeId,
	onOpen,
	onCreate,
	onDelete,
}: {
	sessions: Session[];
	activeId: string | null;
	onOpen: (id: string) => void;
	onCreate: () => void;
	onDelete: (id: string) => void;
}) {
	return (
		<nav className="flex h-full flex-col">
			<div className="border-b border-slate-200 p-3 dark:border-slate-800">
				<button
					type="button"
					onClick={onCreate}
					className="w-full rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900"
				>
					New chat
				</button>
			</div>

			<ul className="min-h-0 flex-1 overflow-y-auto p-2">
				{sessions.length === 0 ? (
					<li className="px-2 py-4 text-xs text-slate-500">No sessions yet.</li>
				) : null}
				{sessions.map((session) => (
					<li key={session.id} className="group flex items-center gap-1">
						<button
							type="button"
							onClick={() => onOpen(session.id)}
							className={
								session.id === activeId
									? "min-w-0 flex-1 truncate rounded px-2 py-2 text-left text-sm bg-slate-200 dark:bg-slate-800"
									: "min-w-0 flex-1 truncate rounded px-2 py-2 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-900"
							}
							title={session.title ?? "Untitled"}
						>
							<span className="block truncate">{session.title ?? "Untitled"}</span>
							<span className="block truncate text-[0.7rem] text-slate-500">
								{session.provider} · {session.model}
							</span>
						</button>
						<button
							type="button"
							onClick={() => onDelete(session.id)}
							title="Delete session"
							aria-label={`Delete ${session.title ?? "session"}`}
							className="rounded px-1.5 py-1 text-xs text-slate-400 opacity-0 hover:text-red-600 focus:opacity-100 group-hover:opacity-100"
						>
							×
						</button>
					</li>
				))}
			</ul>
		</nav>
	);
}
