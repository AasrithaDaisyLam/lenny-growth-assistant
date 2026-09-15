/** Shared contracts. These mirror the backend's Pydantic schemas. */

export interface Citation {
	marker: string;
	chunk_id: number;
	episode_slug: string;
	episode_title: string;
	guest: string | null;
	youtube_url: string | null;
	chunk_index: number;
	speaker: string | null;
}

export interface Message {
	id: number;
	role: "user" | "assistant" | "system";
	content: string;
	intent: string | null;
	citations: Citation[];
	provider: string | null;
	model: string | null;
	latency_ms: number | null;
	created_at: string;
}

export interface Session {
	id: string;
	title: string | null;
	provider: string;
	model: string;
	user_metadata: Record<string, unknown>;
	created_at: string;
	updated_at: string;
	last_message_at: string | null;
}

export interface Provider {
	name: string;
	available: boolean;
	reason: string | null;
	models: string[];
	active: boolean;
}

export interface ProvidersResponse {
	active: string;
	fallback_chain: string[];
	providers: Provider[];
}

export interface HealthComponent {
	ok: boolean;
	error?: string | null;
	[key: string]: unknown;
}

export interface HealthResponse {
	status: string;
	components: Record<string, HealthComponent>;
	config?: Record<string, unknown>;
}

/** A saved artifact, as announced mid-stream by the `artifact` event. */
export interface ArtifactRef {
	artifact_id: string;
	kind?: string;
	title?: string;
}

export interface ArtifactRender {
	id: string;
	session_id: string;
	kind: string;
	title: string | null;
	version: number;
	sanitized_content: string;
	raw_content: string;
	removed_elements: { type: string; name: string; count: number }[];
}

/** A live view of one turn, assembled from SSE frames. */
export interface TurnState {
	text: string;
	citations: Citation[];
	artifacts: ArtifactRef[];
	abstained: { reason: string; topics: string[] } | null;
	tools: { name: string; status: string }[];
	error: { code: string; message: string } | null;
	provider: string | null;
	model: string | null;
	done: boolean;
}

export type StreamEvent =
	| { type: "token"; text: string }
	| { type: "tool"; name: string; status: string; args?: unknown }
	| { type: "citations"; citations: Citation[] }
	| { type: "artifact"; artifact_id: string; kind?: string; title?: string }
	| { type: "abstained"; reason: string; topics: string[] }
	| { type: "usage"; provider: string; model: string }
	| { type: "error"; code: string; message: string }
	| { type: "done"; message_id: number };
