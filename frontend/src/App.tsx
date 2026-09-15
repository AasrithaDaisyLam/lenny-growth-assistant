import { useState } from "react";

import { AppShell } from "./components/AppShell";
import { ChatPanel } from "./components/ChatPanel";
import { HealthBadge } from "./components/HealthBadge";
import { ProviderControl } from "./components/ProviderControl";
import { SessionSidebar } from "./components/SessionSidebar";
import { useChat } from "./hooks/useChat";
import { useProviders } from "./hooks/useProviders";
import { errorText, useSessions } from "./hooks/useSessions";
import type { Citation } from "./types";

export default function App() {
	const sessions = useSessions();
	const { providers, health, error: infraError, refresh: refreshInfra } = useProviders();
	const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);

	const chat = useChat(sessions.activeId, async () => {
		await sessions.reload();
		await sessions.refresh();
	});

	const activeSession =
		sessions.sessions.find((session) => session.id === sessions.activeId) ?? null;

	async function handleSend(content: string) {
		setSelectedCitation(null);
		sessions.setError(null);
		try {
			if (sessions.activeId) {
				await chat.send(content);
			} else {
				// First message of a new conversation: create the session, then send
				// against its id explicitly (the hook has not re-rendered yet).
				const created = await sessions.create();
				await chat.send(content, created.id);
			}
		} catch (cause) {
			sessions.setError(errorText(cause));
		}
	}

	async function handleProviderChange(provider: string) {
		if (!sessions.activeId) return;
		try {
			await sessions.changeProvider(sessions.activeId, provider);
			await refreshInfra();
		} catch (cause) {
			sessions.setError(errorText(cause));
		}
	}

	return (
		<AppShell
			header={
				<>
					<ProviderControl
						providers={providers}
						session={activeSession}
						onChange={(provider) => void handleProviderChange(provider)}
						disabled={chat.streaming}
					/>
					<HealthBadge health={health} error={infraError} />
				</>
			}
			sidebar={
				<SessionSidebar
					sessions={sessions.sessions}
					activeId={sessions.activeId}
					onOpen={(id) => {
						setSelectedCitation(null);
						void sessions.open(id);
					}}
					onCreate={() => {
						setSelectedCitation(null);
						void sessions.create();
					}}
					onDelete={(id) => void sessions.remove(id)}
				/>
			}
		>
			<ChatPanel
				messages={sessions.messages}
				turn={chat.turn}
				streaming={chat.streaming}
				selectedCitation={selectedCitation}
				error={sessions.error}
				onSelectCitation={setSelectedCitation}
				onClearCitation={() => setSelectedCitation(null)}
				onSend={(content) => void handleSend(content)}
				onStop={chat.stop}
				disabled={false}
			/>
		</AppShell>
	);
}
