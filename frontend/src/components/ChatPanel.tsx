import { useEffect, useRef } from "react";

import type { Citation, Message, TurnState } from "../types";
import { SourcePanel } from "./Citations";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";

export function ChatPanel({
	messages,
	turn,
	streaming,
	selectedCitation,
	error,
	onSelectCitation,
	onClearCitation,
	onSend,
	onStop,
	disabled,
}: {
	messages: Message[];
	turn: TurnState | null;
	streaming: boolean;
	selectedCitation: Citation | null;
	error: string | null;
	onSelectCitation: (citation: Citation) => void;
	onClearCitation: () => void;
	onSend: (content: string) => void;
	onStop: () => void;
	disabled: boolean;
}) {
	const scrollRef = useRef<HTMLDivElement>(null);

	// Keep the newest text in view as tokens arrive.
	useEffect(() => {
		const node = scrollRef.current;
		if (node) node.scrollTop = node.scrollHeight;
	}, [turn?.text, messages.length]);

	// Sources shown are the ones for whichever answer is on screen: the message
	// that owns the selected citation, or else all of them for the live turn.
	const sources = selectedCitation
		? [selectedCitation]
		: (turn?.citations ?? []);

	return (
		<>
			<div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
				{error ? (
					<div className="m-3 rounded-lg border border-red-300 bg-red-50 p-3 text-sm dark:border-red-900 dark:bg-red-950">
						{error}
					</div>
				) : null}
				{messages.length === 0 && !turn ? (
					<div className="m-3 rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
						<p className="font-medium text-slate-900 dark:text-slate-100">
							Grounded answers from Lenny's Podcast transcripts.
						</p>
						<p className="mt-1">
							Ask about growth, activation, retention, pricing or leadership. Every claim comes
							back with a citation to the episode it came from, and questions the corpus does not
							cover are declined rather than guessed.
						</p>
					</div>
				) : null}
				<MessageList
					messages={messages}
					turn={turn}
					selected={selectedCitation}
					onSelectCitation={onSelectCitation}
				/>
			</div>

			<SourcePanel
				citations={sources}
				selected={selectedCitation}
				onSelect={onSelectCitation}
				onClose={onClearCitation}
			/>

			<Composer onSend={onSend} onStop={onStop} streaming={streaming} disabled={disabled} />
		</>
	);
}
