import { useCallback, useRef, useState } from "react";

import * as api from "../api/client";
import type { StreamEvent, TurnState } from "../types";
import { errorText } from "./useSessions";

function emptyTurn(): TurnState {
	return {
		text: "",
		citations: [],
		artifacts: [],
		abstained: null,
		tools: [],
		error: null,
		provider: null,
		model: null,
		done: false,
	};
}

function reduce(state: TurnState, event: StreamEvent): TurnState {
	switch (event.type) {
		case "token":
			return { ...state, text: state.text + event.text };
		case "tool":
			return { ...state, tools: [...state.tools, { name: event.name, status: event.status }] };
		case "citations":
			return { ...state, citations: event.citations };
		case "artifact":
			return {
				...state,
				artifacts: [
					...state.artifacts,
					{ artifact_id: event.artifact_id, kind: event.kind, title: event.title },
				],
			};
		case "abstained":
			return { ...state, abstained: { reason: event.reason, topics: event.topics } };
		case "usage":
			return { ...state, provider: event.provider, model: event.model };
		case "error":
			return { ...state, error: { code: event.code, message: event.message } };
		case "done":
			return { ...state, done: true };
		default:
			return state;
	}
}

/**
 * Streams one turn.
 *
 * `onComplete` reloads the persisted transcript, and the live turn is only
 * cleared once that succeeds. Clearing first would flash the answer away and,
 * if the reload failed, lose it entirely.
 */
export function useChat(sessionId: string | null, onComplete: () => Promise<void>) {
	const [turn, setTurn] = useState<TurnState | null>(null);
	const [streaming, setStreaming] = useState(false);
	const abortRef = useRef<AbortController | null>(null);

	const stop = useCallback(() => {
		abortRef.current?.abort();
	}, []);

	const send = useCallback(
		async (content: string, overrideId?: string) => {
			// `overrideId` exists for the first message of a brand-new session: the
			// session is created inside the same handler, so the hook's `sessionId`
			// prop has not re-rendered yet and would still be null.
			const id = overrideId ?? sessionId;
			if (!id || !content.trim()) return;
			const controller = new AbortController();
			abortRef.current = controller;
			setStreaming(true);
			setTurn(emptyTurn());

			try {
				for await (const event of api.sendMessage(id, content, controller.signal)) {
					setTurn((prev) => reduce(prev ?? emptyTurn(), event));
				}
				await onComplete();
				setTurn(null);
			} catch (cause) {
				const aborted = controller.signal.aborted;
				setTurn((prev) => ({
					...(prev ?? emptyTurn()),
					error: {
						code: aborted ? "cancelled" : "client_error",
						message: aborted ? "Stopped." : errorText(cause),
					},
					done: true,
				}));
			} finally {
				setStreaming(false);
				abortRef.current = null;
			}
		},
		[sessionId, onComplete],
	);

	return { turn, streaming, send, stop };
}
