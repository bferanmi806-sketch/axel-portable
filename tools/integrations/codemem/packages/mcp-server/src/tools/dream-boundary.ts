import {
	DREAM_BOUNDARY_CAPABILITY,
	DREAM_BOUNDARY_MAX_CANDIDATES,
	DREAM_BOUNDARY_MAX_EVENTS,
	DREAM_BOUNDARY_MAX_FLUSH_MS,
	flushDreamBoundary,
	getDreamBoundaryEvents,
	getDreamBoundaryStatus,
	resolveProject,
} from "@codemem/core";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { errorContent, jsonContent } from "../content.js";
import type { ToolRegistrationContext } from "../tool-context.js";

const boundaryInput = {
	project: z.string().nullable().optional().describe("Explicit repository-root project identity"),
	cutoff_ms: z
		.number()
		.int()
		.positive()
		.optional()
		.describe("Event cutoff watermark in epoch milliseconds"),
};

function cutoff(value: number | undefined): number {
	return value ?? Date.now();
}

export function registerDreamBoundaryTools(
	server: McpServer,
	context: ToolRegistrationContext,
): void {
	const { store, envProject } = context;
	const repositoryProject = () => resolveProject(process.cwd());
	const selectedProject = (explicit: string | null | undefined) => explicit ?? repositoryProject();
	const scopeMetadata = (project: string | null) => ({
		requested_project: project,
		env_project: envProject(),
		env_project_conflict: Boolean(envProject() && project && envProject() !== project),
	});

	server.tool(
		"codemem_dream_boundary_capabilities",
		"Return the supported Dream Mode boundary capability and limits.",
		{},
		async () =>
			jsonContent({
				capability: DREAM_BOUNDARY_CAPABILITY,
				max_events: DREAM_BOUNDARY_MAX_EVENTS,
				max_flush_ms: DREAM_BOUNDARY_MAX_FLUSH_MS,
				max_candidates: DREAM_BOUNDARY_MAX_CANDIDATES,
			}),
	);

	server.tool(
		"codemem_dream_boundary_status",
		"Inspect pending raw-event state for an explicit project and cutoff.",
		boundaryInput,
		async (args) => {
			try {
				const project = selectedProject(args.project);
				const cutoffMs = cutoff(args.cutoff_ms);
				return jsonContent({
					capability: DREAM_BOUNDARY_CAPABILITY,
					...scopeMetadata(project),
					...getDreamBoundaryStatus(store, project, cutoffMs),
				});
			} catch (err) {
				return errorContent(err instanceof Error ? err.message : String(err));
			}
		},
	);

	server.tool(
		"codemem_dream_boundary_flush",
		"Flush pending raw events at or before a cutoff for a project.",
		{
			...boundaryInput,
			max_events: z
				.number()
				.int()
				.min(1)
				.max(DREAM_BOUNDARY_MAX_EVENTS)
				.default(DREAM_BOUNDARY_MAX_EVENTS),
		},
		async (args) => {
			try {
				const project = selectedProject(args.project);
				const cutoffMs = cutoff(args.cutoff_ms);
				return jsonContent({
					capability: DREAM_BOUNDARY_CAPABILITY,
					...scopeMetadata(project),
					cutoff_ms: cutoffMs,
					...(await flushDreamBoundary(store, project, cutoffMs, args.max_events)),
				});
			} catch (err) {
				return errorContent(err instanceof Error ? err.message : String(err));
			}
		},
	);

	server.tool(
		"codemem_dream_boundary_events",
		"Retrieve bounded sanitized raw events for fallback curation.",
		{
			...boundaryInput,
			since_ms: z.number().int().nonnegative().optional(),
			limit: z
				.number()
				.int()
				.min(1)
				.max(DREAM_BOUNDARY_MAX_EVENTS)
				.default(DREAM_BOUNDARY_MAX_EVENTS),
		},
		async (args) => {
			try {
				const project = selectedProject(args.project);
				const cutoffMs = cutoff(args.cutoff_ms);
				const sinceMs = args.since_ms ?? cutoffMs - 14 * 86_400_000;
				return jsonContent({
					capability: DREAM_BOUNDARY_CAPABILITY,
					...scopeMetadata(project),
					project,
					cutoff_ms: cutoffMs,
					since_ms: sinceMs,
					...getDreamBoundaryEvents(store, project, cutoffMs, sinceMs, args.limit),
				});
			} catch (err) {
				return errorContent(err instanceof Error ? err.message : String(err));
			}
		},
	);
}
