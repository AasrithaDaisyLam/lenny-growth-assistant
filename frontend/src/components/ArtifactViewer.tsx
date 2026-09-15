import { useEffect, useState } from "react";

import * as api from "../api/client";
import type { ArtifactRender } from "../types";
import { errorText } from "../hooks/useSessions";

/**
 * Render untrusted, model-generated HTML.
 *
 * `sandbox="allow-scripts"` and deliberately **not** `allow-same-origin`. Combining
 * the two is the specific mistake that defeats iframe sandboxing: it lets the frame
 * script reach the parent origin, so the sandbox stops containing anything. With
 * scripts alone the frame gets an opaque origin, which means no cookies, no
 * localStorage, and no access to this document.
 *
 * `srcDoc` (rather than a URL) is what produces that opaque origin.
 */
export function SandboxFrame({ html }: { html: string }) {
	return (
		<iframe
			title="Artifact preview"
			sandbox="allow-scripts"
			srcDoc={html}
			className="h-80 w-full rounded border border-slate-200 bg-white dark:border-slate-800"
		/>
	);
}

/** Preview / source, plus a visible account of anything sanitization removed. */
export function ArtifactViewer({ artifactId }: { artifactId: string }) {
	const [artifact, setArtifact] = useState<ArtifactRender | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [showSource, setShowSource] = useState(false);

	useEffect(() => {
		let cancelled = false;
		api
			.getArtifact(artifactId)
			.then((result) => {
				if (!cancelled) setArtifact(result);
			})
			.catch((cause) => {
				if (!cancelled) setError(errorText(cause));
			});
		return () => {
			cancelled = true;
		};
	}, [artifactId]);

	if (error) {
		return (
			<div className="rounded border border-red-300 bg-red-50 p-2 text-xs dark:border-red-900 dark:bg-red-950">
				{error}
			</div>
		);
	}
	if (!artifact) {
		return <div className="p-2 text-xs text-slate-500">loading artifact…</div>;
	}

	const removed = artifact.removed_elements ?? [];

	return (
		<div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-800 dark:bg-slate-900">
			<div className="mb-2 flex flex-wrap items-center gap-2">
				<span className="text-sm font-medium">{artifact.title ?? "Artifact"}</span>
				<span className="rounded bg-slate-100 px-1.5 py-0.5 text-[0.7rem] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
					{artifact.kind} · v{artifact.version}
				</span>
				<div className="ml-auto flex gap-1 text-xs">
					<button
						type="button"
						onClick={() => setShowSource(false)}
						className={showSource ? "text-slate-500" : "font-medium text-sky-700 dark:text-sky-300"}
					>
						Preview
					</button>
					<button
						type="button"
						onClick={() => setShowSource(true)}
						className={showSource ? "font-medium text-sky-700 dark:text-sky-300" : "text-slate-500"}
					>
						Source
					</button>
				</div>
			</div>

			{removed.length > 0 ? (
				<div className="mb-2 rounded border border-amber-300 bg-amber-50 p-2 text-[0.7rem] dark:border-amber-800 dark:bg-amber-950">
					<div className="font-medium text-amber-900 dark:text-amber-200">
						Removed before display:
					</div>
					<ul className="mt-0.5 list-inside list-disc text-amber-800 dark:text-amber-300">
						{removed.map((item) => (
							<li key={`${item.type}:${item.name}`}>
								{item.name} ({item.type}
								{item.count > 1 ? ` ×${item.count}` : ""})
							</li>
						))}
					</ul>
				</div>
			) : null}

			{showSource ? (
				<pre className="max-h-80 overflow-auto rounded bg-slate-50 p-2 text-[0.7rem] whitespace-pre-wrap dark:bg-slate-950">
					{artifact.raw_content}
				</pre>
			) : artifact.kind === "html" ? (
				<SandboxFrame html={artifact.sanitized_content} />
			) : (
				// Markdown is shown preformatted rather than rendered. It is a deliberate
				// simplification: rendering it would mean pulling in a markdown pipeline,
				// and showing the source is never unsafe. The upgrade path is
				// react-markdown with rehype-sanitize.
				<pre className="max-h-80 overflow-auto rounded bg-slate-50 p-2 text-xs whitespace-pre-wrap dark:bg-slate-950">
					{artifact.sanitized_content}
				</pre>
			)}
		</div>
	);
}
