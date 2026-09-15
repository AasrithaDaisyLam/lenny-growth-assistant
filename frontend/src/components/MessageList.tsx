import type { ReactNode } from "react";

import type { Citation, Message, TurnState } from "../types";
import { ArtifactViewer } from "./ArtifactViewer";
import { CitationChip } from "./Citations";

/**
 * Replace `[S#]` markers with chips.
 *
 * The backend strips markers it cannot resolve, so every marker still present
 * has a matching citation; anything unmatched is rendered as plain text rather
 * than as a dead chip.
 */
function withCitations(
	text: string,
	citations: Citation[],
	selected: Citation | null,
	onSelect: (citation: Citation) => void,
) {
	const byMarker = new Map(citations.map((citation) => [citation.marker, citation]));
	return text.split(/(\[S\d+\])/g).map((part, index) => {
		const match = /^\[S(\d+)\]$/.exec(part);
		const citation = match ? byMarker.get(`S${match[1]}`) : undefined;
		if (!citation) return <span key={index}>{part}</span>;
		return (
			<CitationChip
				key={index}
				citation={citation}
				active={selected?.marker === citation.marker}
				onSelect={onSelect}
			/>
		);
	});
}

function Bubble({
	role,
	children,
	meta,
}: {
	role: "user" | "assistant";
	children: ReactNode;
	meta?: ReactNode;
}) {
	const isUser = role === "user";
	return (
		<div className={isUser ? "flex justify-end" : "flex justify-start"}>
			<div
				className={
					isUser
						? "max-w-[85%] rounded-lg bg-sky-600 px-3 py-2 text-sm whitespace-pre-wrap text-white"
						: "max-w-[85%] rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm whitespace-pre-wrap dark:border-slate-800 dark:bg-slate-900"
				}
			>
				{children}
				{meta ? <div className="mt-1 text-[0.7rem] text-slate-400">{meta}</div> : null}
			</div>
		</div>
	);
}

export function MessageList({
	messages,
	turn,
	selected,
	onSelectCitation,
}: {
	messages: Message[];
	turn: TurnState | null;
	selected: Citation | null;
	onSelectCitation: (citation: Citation) => void;
}) {
	return (
		<div className="space-y-3 p-3">
			{messages.map((message) => (
				<Bubble
					key={message.id}
					role={message.role === "user" ? "user" : "assistant"}
					meta={
						message.role === "assistant" && message.latency_ms
							? `${(message.latency_ms / 1000).toFixed(1)}s`
							: undefined
					}
				>
					{withCitations(message.content, message.citations, selected, onSelectCitation)}
				</Bubble>
			))}

			{turn ? (
				<>
					{turn.tools.length > 0 ? (
						<div className="text-[0.7rem] text-slate-500">
							tools:{" "}
							{turn.tools
								.map((tool) => `${tool.name}(${tool.status})`)
								.join(", ")}
						</div>
					) : null}

					{turn.text ? (
						<Bubble role="assistant">
							{withCitations(turn.text, turn.citations, selected, onSelectCitation)}
							{!turn.done ? <span className="ml-0.5 animate-pulse">▍</span> : null}
						</Bubble>
					) : turn.done ? null : (
						<div className="text-xs text-slate-500">retrieving…</div>
					)}

					{turn.artifacts.map((artifact) => (
						<ArtifactViewer key={artifact.artifact_id} artifactId={artifact.artifact_id} />
					))}

					{turn.abstained ? (
						<div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950">
							<div className="font-medium">Not covered by the transcripts.</div>
							{turn.abstained.topics.length > 0 ? (
								<div className="mt-1 text-xs">
									It does cover: {turn.abstained.topics.slice(0, 8).join(", ")}.
								</div>
							) : null}
						</div>
					) : null}

					{turn.error ? (
						<div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm dark:border-red-900 dark:bg-red-950">
							<div className="font-medium">{turn.error.code}</div>
							<div className="mt-1 text-xs break-words">{turn.error.message}</div>
						</div>
					) : null}
				</>
			) : null}
		</div>
	);
}
