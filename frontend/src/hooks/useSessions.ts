import { useCallback, useEffect, useState } from "react";

import * as api from "../api/client";
import type { Message, Session } from "../types";

export function errorText(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

export function useSessions() {
	const [sessions, setSessions] = useState<Session[]>([]);
	const [activeId, setActiveId] = useState<string | null>(null);
	const [messages, setMessages] = useState<Message[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const refresh = useCallback(async () => {
		try {
			setSessions(await api.listSessions());
		} catch (cause) {
			setError(errorText(cause));
		}
	}, []);

	useEffect(() => {
		void refresh();
	}, [refresh]);

	const open = useCallback(async (id: string) => {
		setActiveId(id);
		setLoading(true);
		setError(null);
		try {
			setMessages(await api.listMessages(id));
		} catch (cause) {
			setError(errorText(cause));
		} finally {
			setLoading(false);
		}
	}, []);

	const reload = useCallback(async () => {
		if (!activeId) return;
		try {
			setMessages(await api.listMessages(activeId));
		} catch (cause) {
			setError(errorText(cause));
		}
	}, [activeId]);

	const create = useCallback(async () => {
		const session = await api.createSession();
		setSessions((prev) => [session, ...prev]);
		setActiveId(session.id);
		setMessages([]);
		return session;
	}, []);

	const remove = useCallback(
		async (id: string) => {
			await api.deleteSession(id);
			setSessions((prev) => prev.filter((session) => session.id !== id));
			if (activeId === id) {
				setActiveId(null);
				setMessages([]);
			}
		},
		[activeId],
	);

	const rename = useCallback(async (id: string, title: string) => {
		const updated = await api.updateSession(id, { title });
		setSessions((prev) => prev.map((s) => (s.id === id ? updated : s)));
	}, []);

	const changeProvider = useCallback(
		async (id: string, provider: string) => {
			// Applies from the next turn without a restart; the backend switches the
			// live agent process over RPC.
			const updated = await api.updateSession(id, { provider });
			setSessions((prev) => prev.map((s) => (s.id === id ? updated : s)));
			return updated;
		},
		[activeId],
	);

	return {
		sessions,
		activeId,
		messages,
		loading,
		error,
		setError,
		refresh,
		reload,
		open,
		create,
		remove,
		rename,
		changeProvider,
	};
}
