import { ObserverClient } from "./observer-client.js";
import { type FlushRawEventsOptions, flushRawEvents } from "./raw-event-flush.js";
import type { MemoryStore } from "./store.js";

export const DREAM_BOUNDARY_CAPABILITY = "codemem.dream-boundary/v1" as const;
export const DREAM_BOUNDARY_MAX_EVENTS = 2_000;
export const DREAM_BOUNDARY_MAX_CANDIDATES = 10;
export const DREAM_BOUNDARY_MAX_FLUSH_MS = 30_000;

export interface DreamBoundaryEvent {
	event_id: string | null;
	session_id: string;
	project_id: string | null;
	occurred_at: string | null;
	event_type: string;
	payload: Record<string, unknown>;
}

export interface DreamBoundaryStatus {
	requested_project: string | null;
	resolved_projects: string[];
	project_match: boolean;
	cutoff_ms: number;
	pending_before: number;
	oldest_pending_at: string | null;
	sessions: number;
}

function isSensitiveKey(key: string): boolean {
	return /(token|secret|password|credential|private.?key|authorization|cookie|api.?key|header)/i.test(
		key,
	);
}

function sanitizeValue(value: unknown, depth = 0): unknown {
	if (depth > 4) return "[truncated]";
	if (typeof value === "string") {
		if (/-----BEGIN .*PRIVATE KEY-----|bearer\s+[a-z0-9._-]+|sk-[a-z0-9]{12,}/i.test(value)) {
			return "[redacted]";
		}
		return value.length > 2_000 ? `${value.slice(0, 1_997)}...` : value;
	}
	if (Array.isArray(value))
		return value.slice(0, 100).map((item) => sanitizeValue(item, depth + 1));
	if (value && typeof value === "object") {
		const result: Record<string, unknown> = {};
		for (const [key, item] of Object.entries(value)) {
			if (isSensitiveKey(key)) continue;
			result[key] = sanitizeValue(item, depth + 1);
		}
		return result;
	}
	return value;
}

export function sanitizeDreamBoundaryPayload(payload: unknown): Record<string, unknown> {
	const result = sanitizeValue(payload);
	return result && typeof result === "object" && !Array.isArray(result)
		? (result as Record<string, unknown>)
		: { value: result };
}

export function getDreamBoundaryStatus(
	store: MemoryStore,
	project: string | null,
	cutoffMs: number,
): DreamBoundaryStatus {
	const rows = store.db
		.prepare(
			`SELECT s.project, s.last_flushed_event_seq, r.event_seq, r.ts_wall_ms, r.created_at
			 FROM raw_event_sessions s
			 LEFT JOIN raw_events r
			   ON r.source = s.source AND r.stream_id = s.stream_id
			 WHERE (? IS NULL OR s.project = ?)
			 ORDER BY r.ts_wall_ms ASC, r.created_at ASC`,
		)
		.all(project, project) as Array<{
		project: string | null;
		last_flushed_event_seq: number;
		event_seq: number | null;
		ts_wall_ms: number | null;
		created_at: string | null;
	}>;
	const projects = [
		...new Set(rows.map((row) => row.project).filter((value): value is string => Boolean(value))),
	];
	const pending = rows.filter(
		(row) =>
			row.event_seq != null &&
			row.event_seq > row.last_flushed_event_seq &&
			(row.ts_wall_ms == null || row.ts_wall_ms <= cutoffMs),
	);
	return {
		requested_project: project,
		resolved_projects: projects,
		project_match: project == null || projects.includes(project),
		cutoff_ms: cutoffMs,
		pending_before: pending.length,
		oldest_pending_at: pending[0]?.created_at ?? null,
		sessions: new Set(rows.map((row) => row.project ?? "")).size,
	};
}

export function getDreamBoundaryEvents(
	store: MemoryStore,
	project: string | null,
	cutoffMs: number,
	sinceMs: number,
	limit = DREAM_BOUNDARY_MAX_EVENTS,
): { events: DreamBoundaryEvent[]; truncated: boolean } {
	const safeLimit = Math.min(Math.max(1, limit), DREAM_BOUNDARY_MAX_EVENTS);
	const rows = store.db
		.prepare(
			`SELECT r.event_id, r.opencode_session_id, s.project, r.ts_wall_ms, r.created_at, r.event_type, r.payload_json
			 FROM raw_events r
			 LEFT JOIN raw_event_sessions s ON s.source = r.source AND s.stream_id = r.stream_id
			 WHERE (? IS NULL OR s.project = ?)
			   AND (r.ts_wall_ms IS NULL OR r.ts_wall_ms BETWEEN ? AND ?)
			 ORDER BY COALESCE(r.ts_wall_ms, 0) ASC, r.created_at ASC
			 LIMIT ?`,
		)
		.all(project, project, sinceMs, cutoffMs, safeLimit + 1) as Array<{
		event_id: string | null;
		opencode_session_id: string;
		project: string | null;
		ts_wall_ms: number | null;
		created_at: string | null;
		event_type: string;
		payload_json: string;
	}>;
	const truncated = rows.length > safeLimit;
	return {
		events: rows.slice(0, safeLimit).map((row) => {
			let payload: unknown;
			try {
				payload = JSON.parse(row.payload_json);
			} catch {
				payload = { parse_error: true };
			}
			return {
				event_id: row.event_id,
				session_id: row.opencode_session_id,
				project_id: row.project,
				occurred_at:
					row.ts_wall_ms == null ? row.created_at : new Date(row.ts_wall_ms).toISOString(),
				event_type: row.event_type,
				payload: sanitizeDreamBoundaryPayload(payload),
			};
		}),
		truncated,
	};
}

export async function flushDreamBoundary(
	store: MemoryStore,
	project: string | null,
	cutoffMs: number,
	maxEvents = DREAM_BOUNDARY_MAX_EVENTS,
): Promise<{
	attempted: number;
	completed: number;
	pending_after: number;
	timed_out: boolean;
	error: string | null;
}> {
	const started = Date.now();
	const sessions = store.db
		.prepare(
			`SELECT DISTINCT s.source, s.stream_id, s.opencode_session_id, s.project
			 FROM raw_event_sessions s JOIN raw_events r
			 ON r.source = s.source AND r.stream_id = s.stream_id
			 WHERE (? IS NULL OR s.project = ?)
			   AND r.event_seq > s.last_flushed_event_seq
			   AND (r.ts_wall_ms IS NULL OR r.ts_wall_ms <= ?)`,
		)
		.all(project, project, cutoffMs) as Array<{
		source: string;
		stream_id: string;
		opencode_session_id: string;
		project: string | null;
	}>;
	let completed = 0;
	let error: string | null = null;
	try {
		const observer = new ObserverClient();
		for (const session of sessions) {
			if (Date.now() - started >= DREAM_BOUNDARY_MAX_FLUSH_MS) break;
			const opts: FlushRawEventsOptions & { untilTsWallMs?: number } = {
				opencodeSessionId: session.stream_id || session.opencode_session_id,
				source: session.source,
				project: session.project,
				maxEvents,
				untilTsWallMs: cutoffMs,
			};
			await flushRawEvents(store, { observer }, opts);
			completed += 1;
		}
	} catch (caught) {
		error = caught instanceof Error ? caught.message : String(caught);
	}
	const status = getDreamBoundaryStatus(store, project, cutoffMs);
	return {
		attempted: sessions.length,
		completed,
		pending_after: status.pending_before,
		timed_out: Date.now() - started >= DREAM_BOUNDARY_MAX_FLUSH_MS,
		error,
	};
}
