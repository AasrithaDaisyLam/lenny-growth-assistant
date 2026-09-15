import { useState, type ReactNode } from "react";

/**
 * Two-pane shell.
 *
 * Below `md` the sidebar becomes an overlay so the transcript gets the full
 * 360px width; from `md` up it is a fixed column. The transcript scrolls
 * independently of the page, which is why the middle wrapper is `min-h-0` --
 * without it a flex child refuses to shrink and the page scrolls instead.
 */
export function AppShell({
	sidebar,
	header,
	children,
}: {
	sidebar: ReactNode;
	header: ReactNode;
	children: ReactNode;
}) {
	const [navOpen, setNavOpen] = useState(false);

	return (
		<div className="flex h-full flex-col bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
			<header className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-slate-950">
				<button
					type="button"
					onClick={() => setNavOpen((open) => !open)}
					aria-label="Toggle sessions"
					aria-expanded={navOpen}
					className="rounded p-1 text-slate-600 hover:bg-slate-100 md:hidden dark:text-slate-300 dark:hover:bg-slate-900"
				>
					☰
				</button>
				<h1 className="truncate text-sm font-semibold">The Lenny Growth Assistant</h1>
				<div className="ml-auto flex items-center gap-2">{header}</div>
			</header>

			<div className="relative flex min-h-0 flex-1">
				<div
					className={
						navOpen
							? "absolute inset-y-0 left-0 z-20 w-72 border-r border-slate-200 bg-white shadow-xl dark:border-slate-800 dark:bg-slate-950 md:static md:shadow-none"
							: "hidden md:block md:w-72 md:shrink-0 md:border-r md:border-slate-200 md:dark:border-slate-800"
					}
				>
					{sidebar}
				</div>
				<main className="flex min-w-0 flex-1 flex-col">{children}</main>
			</div>
		</div>
	);
}
