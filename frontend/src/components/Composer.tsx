import { useState } from "react";

/**
 * The composer.
 *
 * Enter sends, Shift+Enter adds a newline -- the convention for chat, and the
 * reason this is a textarea rather than an input. While a turn streams the send
 * button becomes Stop, so a slow CPU turn is always interruptible.
 */
export function Composer({
	onSend,
	onStop,
	streaming,
	disabled,
}: {
	onSend: (content: string) => void;
	onStop: () => void;
	streaming: boolean;
	disabled: boolean;
}) {
	const [value, setValue] = useState("");
	const canSend = !disabled && !streaming && value.trim().length > 0;

	function submit() {
		if (!canSend) return;
		onSend(value.trim());
		setValue("");
	}

	return (
		<form
			className="flex items-end gap-2 border-t border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-950"
			onSubmit={(event) => {
				event.preventDefault();
				submit();
			}}
		>
			<textarea
				value={value}
				onChange={(event) => setValue(event.target.value)}
				onKeyDown={(event) => {
					if (event.key === "Enter" && !event.shiftKey) {
						event.preventDefault();
						submit();
					}
				}}
				rows={1}
				placeholder={
					disabled ? "Start a session to begin" : "Ask about growth, activation, retention…"
				}
				disabled={disabled}
				className="max-h-40 min-h-[2.5rem] flex-1 resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900"
			/>
			{streaming ? (
				<button
					type="button"
					onClick={onStop}
					className="rounded-md bg-slate-200 px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-300 dark:bg-slate-800 dark:text-slate-100"
				>
					Stop
				</button>
			) : (
				<button
					type="submit"
					disabled={!canSend}
					className="rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-40"
				>
					Send
				</button>
			)}
		</form>
	);
}
