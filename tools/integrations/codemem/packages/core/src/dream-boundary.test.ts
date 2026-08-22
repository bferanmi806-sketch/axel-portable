import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { connect } from "./db.js";
import { getDreamBoundaryEvents, getDreamBoundaryStatus } from "./dream-boundary.js";
import { MemoryStore } from "./store.js";
import { initTestSchema } from "./test-utils.js";

describe("dream boundary", () => {
	let tmpDir: string;
	let store: MemoryStore;

	beforeEach(() => {
		tmpDir = mkdtempSync(join(tmpdir(), "codemem-dream-boundary-"));
		const dbPath = join(tmpDir, "test.sqlite");
		const db = connect(dbPath);
		initTestSchema(db);
		db.close();
		store = new MemoryStore(dbPath);
		store.recordRawEvent({
			opencodeSessionId: "session-1",
			eventId: "event-1",
			eventType: "user_prompt",
			payload: { type: "user_prompt", prompt_text: "choose a safe design", api_token: "secret" },
			tsWallMs: 1_000,
		});
		store.recordRawEvent({
			opencodeSessionId: "session-1",
			eventId: "event-2",
			eventType: "assistant_message",
			payload: { type: "assistant_message", text: "later" },
			tsWallMs: 3_000,
		});
		store.db
			.prepare("UPDATE raw_event_sessions SET project = ? WHERE stream_id = ?")
			.run("repo", "session-1");
	});

	afterEach(() => {
		store.close();
		rmSync(tmpDir, { recursive: true, force: true });
	});

	it("reports project-scoped pending state at a cutoff", () => {
		const result = getDreamBoundaryStatus(store, "repo", 2_000);
		expect(result.project_match).toBe(true);
		expect(result.pending_before).toBe(1);
		expect(result.sessions).toBe(1);
	});

	it("bounds and sanitizes fallback events", () => {
		const result = getDreamBoundaryEvents(store, "repo", 2_000, 0, 2_000);
		expect(result.truncated).toBe(false);
		expect(result.events).toHaveLength(1);
		expect(result.events[0]?.payload.api_token).toBeUndefined();
		expect(result.events[0]?.event_id).toBe("event-1");
	});
});
