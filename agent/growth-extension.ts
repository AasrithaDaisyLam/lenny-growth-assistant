/**
 * The Growth Assistant tool surface for the Pi Coding Agent.
 *
 * Pi is a *coding* agent. Its built-in `read`/`bash`/`edit`/`write` tools are
 * actively wrong here, and they are removed by the `--no-builtin-tools` +
 * `--tools` flags rather than by prompt instruction. That matters: it is the
 * difference between "answers come only from the transcripts" being a
 * *configuration guarantee* and being a polite request the model may ignore.
 *
 * This extension is deliberately a thin HTTP shim. All retrieval and persistence
 * intelligence stays in Python behind the API, so there is exactly one
 * implementation of RAG and one implementation of sanitization, rather than two
 * that can disagree.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Type } from "typebox";

const BASE_URL = process.env.INTERNAL_TOOL_BASE_URL ?? "http://localhost:8000";
const TOKEN = process.env.INTERNAL_TOOL_TOKEN ?? "";
// Set by the backend pool when it spawns this process: one process serves one
// session, which is what attributes a saved artifact to the right conversation.
const SESSION_ID = process.env.PI_SESSION_ID ?? "";

interface SearchHit {
	/** The `[S#]` marker this passage is citable by within the running turn. */
	marker: string;
	chunk_id: number;
	episode_slug: string;
	episode_title: string;
	guest: string | null;
	speaker: string | null;
	score: number;
	similarity: number | null;
	text: string;
}

interface SearchPayload {
	query: string;
	count: number;
	results: SearchHit[];
}

interface EpisodePayload {
	slug: string;
	title: string;
	guest: string | null;
	youtube_url: string | null;
	chunk_count: number;
	transcript: string;
}

interface ArtifactSaved {
	artifact_id: string;
	kind: string;
	title: string | null;
	version: number;
	removed_count: number;
}

async function callInternal<T>(
	path: string,
	body: unknown,
	extraHeaders: Record<string, string> = {},
): Promise<T> {
	const response = await fetch(`${BASE_URL}${path}`, {
		method: "POST",
		headers: {
			"content-type": "application/json",
			"x-internal-token": TOKEN,
			...extraHeaders,
		},
		body: JSON.stringify(body),
	});

	if (!response.ok) {
		// Surface the status: a swallowed failure would look to the model like
		// "the transcripts contain nothing", which is the opposite of the truth.
		const detail = await response.text().catch(() => "");
		throw new Error(`internal tool ${path} returned ${response.status}: ${detail.slice(0, 300)}`);
	}
	return (await response.json()) as T;
}

function formatHits(payload: SearchPayload): string {
	if (payload.count === 0) {
		return `No passages matched "${payload.query}".`;
	}
	return payload.results
		.map((hit) => {
			const who = hit.speaker ? `${hit.speaker} in ` : "";
			// The marker is what makes a fetched passage citable, so it is printed
			// with the passage rather than only held server-side.
			const label = hit.marker ? `[${hit.marker}] ` : "";
			return [
				`--- ${label}${who}${hit.episode_title} (episode: ${hit.episode_slug})`,
				hit.text,
			].join("\n");
		})
		.join("\n\n");
}

export default function growthExtension(pi: ExtensionAPI) {
	pi.registerTool({
		name: "search_transcripts",
		label: "Search transcripts",
		description:
			"Search Lenny's Podcast transcripts for passages relevant to a query. " +
			"Use when the numbered sources already provided do not cover the question.",
		promptSnippet: "Search the podcast transcripts for additional relevant passages",
		promptGuidelines: [
			"Use search_transcripts when the provided numbered sources do not cover the question, " +
				"or when a follow-up needs evidence the earlier sources did not include.",
			"Use get_episode when a passage needs its surrounding episode context to be interpreted correctly.",
			"Cite a passage you fetched by the [S#] marker printed with it, exactly as shown.",
		],
		parameters: Type.Object({
			query: Type.String({
				description: "Natural-language description of what to look for.",
			}),
			k: Type.Optional(
				Type.Number({
					description: "How many passages to return. Defaults to 6.",
					minimum: 1,
					maximum: 20,
				}),
			),
		}),
		async execute(_toolCallId, params) {
			const payload = await callInternal<SearchPayload>(
				"/internal/tools/search_transcripts",
				{ query: params.query, k: params.k },
				{ "x-session-id": SESSION_ID },
			);
			return {
				content: [{ type: "text" as const, text: formatHits(payload) }],
				details: { count: payload.count },
			};
		},
	});

	pi.registerTool({
		name: "get_episode",
		label: "Get episode",
		description:
			"Fetch the full transcript of one episode by its slug, for context a single passage lacks.",
		promptSnippet: "Fetch a full episode transcript by slug",
		parameters: Type.Object({
			slug: Type.String({
				description: "Episode slug, e.g. 'elena-verna-40'.",
			}),
		}),
		async execute(_toolCallId, params) {
			const payload = await callInternal<EpisodePayload>(
				"/internal/tools/get_episode",
				{ slug: params.slug },
				{ "x-session-id": SESSION_ID },
			);
			const header = [
				`# ${payload.title}`,
				payload.guest ? `Guest: ${payload.guest}` : "",
				"",
			]
				.filter(Boolean)
				.join("\n");
			return {
				content: [{ type: "text" as const, text: `${header}\n${payload.transcript}` }],
				details: { slug: payload.slug, chunkCount: payload.chunk_count },
			};
		},
	});

	pi.registerTool({
		name: "save_artifact",
		label: "Save artifact",
		description:
			"Save a self-contained artifact (markdown, or one HTML document) that the user can " +
			"view. Use when the answer is better shown than described, such as a chart or a " +
			"formatted brief.",
		promptSnippet: "Save a markdown or HTML artifact for the user to view",
		promptGuidelines: [
			"Use save_artifact when the user asks for a document, a chart, or formatted output.",
			"Artifacts must be self-contained: inline styles and data: images only. Remote URLs, " +
				"scripts loaded from a network, and forms are stripped before the user sees them.",
		],
		parameters: Type.Object({
			kind: StringEnum(["markdown", "html"] as const),
			title: Type.Optional(Type.String({ description: "Short title for the artifact." })),
			content: Type.String({ description: "The full artifact source." }),
		}),
		async execute(_toolCallId, params) {
			const payload = await callInternal<ArtifactSaved>(
				"/internal/tools/save_artifact",
				{ kind: params.kind, title: params.title, content: params.content },
				{ "x-session-id": SESSION_ID },
			);
			// Returned as JSON text so the API can turn this tool result into an
			// `artifact` SSE event without a second lookup, and so the model can see
			// what was stripped rather than assuming it was saved verbatim.
			return {
				content: [
					{
						type: "text" as const,
						text: JSON.stringify({
							artifact_id: payload.artifact_id,
							version: payload.version,
							removed_count: payload.removed_count,
						}),
					},
				],
				details: {
					artifactId: payload.artifact_id,
					removedCount: payload.removed_count,
				},
			};
		},
	});
}
