import type {
	ArtifactRender,
	HealthResponse,
	Message,
	ProvidersResponse,
	Session,
	StreamEvent,
} from "../types";

// Baked at build time. "/api" in the container (nginx proxies it) and via the
// Vite dev proxy locally.
const BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function failure(response: Response): Promise<string> {
	try {
		const body = await response.json();
		const message = body?.error?.message;
		if (typeof message === "string") return message;
	} catch {
		// fall through to the status text
	}
	return `${response.status} ${response.statusText}`;
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
	const response = await fetch(`${BASE}${path}`, init);
	if (!response.ok) throw new Error(await failure(response));
	return (await response.json()) as T;
}

function jsonInit(method: string, body?: unknown): RequestInit {
	return {
		method,
		headers: { "Content-Type": "application/json" },
		body: body === undefined ? undefined : JSON.stringify(body),
	};
}

// --- Sessions ---------------------------------------------------------------

export const listSessions = () => getJson<Session[]>("/sessions");

export const createSession = () =>
	getJson<Session>("/sessions", jsonInit("POST", {}));

export const updateSession = (id: string, patch: { title?: string; provider?: string }) =>
	getJson<Session>(`/sessions/${id}`, jsonInit("PATCH", patch));

export async function deleteSession(id: string): Promise<void> {
	const response = await fetch(`${BASE}/sessions/${id}`, { method: "DELETE" });
	if (!response.ok) throw new Error(await failure(response));
}

export const listMessages = (id: string) => getJson<Message[]>(`/sessions/${id}/messages`);

// --- Catalogue and health ---------------------------------------------------

export const getProviders = () => getJson<ProvidersResponse>("/providers");

export const getHealth = () => getJson<HealthResponse>("/health/ready");

// --- Artifacts --------------------------------------------------------------

export const getArtifact = (artifactId: string) =>
	getJson<ArtifactRender>(`/artifacts/${artifactId}`);

// --- Streaming --------------------------------------------------------------

function parseFrame(frame: string): StreamEvent | null {
	let name = "";
	let data = "";
	// A frame is `event: <name>` followed by `data: <json>`. Blank lines are
	// separators and are stripped by the caller.
	for (const rawLine of frame.split("\n")) {
		const line = rawLine.replace(/\r$/, "");
		if (line.startsWith("event:")) name = line.slice(6).trim();
		else if (line.startsWith("data:")) data += line.slice(5).trim();
	}
	if (!name || !data) return null;
	try {
		return { type: name, ...JSON.parse(data) } as StreamEvent;
	} catch {
		return null;
	}
}

/**
 * Stream one turn as SSE.
 *
 * Frames are separated by a blank line, so the buffer is split on "\n\n" rather
 * than read line by line: a single `data:` payload can be split across chunks,
 * and a line-based reader would emit a truncated JSON document.
 */
export async function* sendMessage(
	sessionId: string,
	content: string,
	signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
	const response = await fetch(`${BASE}/sessions/${sessionId}/messages`, {
		...jsonInit("POST", { content }),
		signal,
	});
	if (!response.ok || !response.body) throw new Error(await failure(response));

	const reader = response.body.getReader();
	const decoder = new TextDecoder();
	let buffer = "";

	try {
		while (true) {
			const { value, done } = await reader.read();
			if (done) break;
			buffer += decoder.decode(value, { stream: true });

			let boundary = buffer.indexOf("\n\n");
			while (boundary !== -1) {
				const frame = buffer.slice(0, boundary);
				buffer = buffer.slice(boundary + 2);
				const event = parseFrame(frame);
				if (event) yield event;
				boundary = buffer.indexOf("\n\n");
			}
		}
	} finally {
		reader.releaseLock();
	}
}
