import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createAdapter } from "../axel-capture.js";

const fixture = JSON.parse(await readFile(new URL("../fixtures/events.json", import.meta.url), "utf8"));

function adapterWith(calls = {}) {
  const writes = [];
  const adapter = createAdapter({}, {
    root: "runtime",
    ensureDirectory: async () => {},
    writeFile: async (path, body) => { writes.push({ path, body }); },
    syncFile: async () => {},
    replaceFile: async (temporary, target) => {
      const entry = writes.find((item) => item.path === temporary);
      if (!entry) throw new Error("temporary spool file was not written");
      entry.path = target;
    },
    runEngine: calls.runEngine || (async () => true),
  });
  return { adapter, writes };
}

async function waitFor(check, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.fail("timed out waiting for asynchronous adapter work");
}

test("normalizes documented event and tool hooks into one bounded envelope", async () => {
  const { adapter, writes } = adapterWith();
  await adapter.event({ event: fixture[0] });
  await adapter.event({ event: fixture[1] });
  await adapter.event({ event: fixture[2] });
  await adapter["tool.execute.before"]({ sessionID: "ses-fixture-01", tool: "bash", callID: "call-01" }, { args: { command: "npm test", api_key: "sk-test-secret-123456789", private_key: "-----BEGIN PRIVATE KEY-----secret-----END PRIVATE KEY-----", path: ".env", message: "/home/example/.aws/credentials before Cookie: sessionid=super-secret-cookie" } });
  await adapter["tool.execute.after"]({ sessionID: "ses-fixture-01", tool: "bash", callID: "call-01", args: { command: "npm test" } }, { output: "x".repeat(3000) });
  await adapter.event({ event: fixture[3] });
  await adapter.event({ event: fixture[4] });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(writes.length, 1);
  const record = JSON.parse(writes[0].body);
  assert.equal(record.schema_version, 1);
  assert.equal(record.host, "opencode");
  assert.equal(record.host_version, "unknown");
  assert.equal(record.events.length, 4);
  const rendered = JSON.stringify(record);
  assert.match(rendered, /\[REDACTED:sensitive field\]/);
  assert.doesNotMatch(rendered, /BEGIN PRIVATE KEY/);
  assert.doesNotMatch(rendered, /\.env/);
  assert.doesNotMatch(rendered, /\.aws\/credentials/);
  assert.doesNotMatch(rendered, /sessionid=super-secret-cookie/);
  assert.ok(rendered.length < 10000);
});

test("redacts nested scalar secrets and authorization headers before spooling", async () => {
  const { adapter, writes } = adapterWith();
  await adapter["tool.execute.before"](
    { sessionID: "ses-secret-boundary", tool: "bash", callID: "call-secret-boundary" },
    {
      args: {
        api_key: { value: "nested-secret" },
        password: 123456,
        passwd: "direct-passwd",
        message: "Authorization: Basic dXNlcjpzdXBlci1zZWNyZXQ=",
        compound: "client_secret=fixture-secret-123 OPENAI_API_KEY=fixture-key-789 clientSecret=\"fixture quoted secret\" {\"clientSecret\":\"escaped \\\"secret\\\"\"}",
        headers: [{ name: "Authorization", value: "Basic structured-secret" }],
      },
    },
  );
  await adapter.event({ event: { type: "session.idle", properties: { sessionID: "ses-secret-boundary" } } });
  await new Promise((resolve) => setImmediate(resolve));

  const rendered = writes[0].body;
  assert.doesNotMatch(rendered, /nested-secret/);
  assert.doesNotMatch(rendered, /123456/);
  assert.doesNotMatch(rendered, /dXNlcjpzdXBlci1zZWNyZXQ=/);
  assert.doesNotMatch(rendered, /structured-secret/);
  assert.doesNotMatch(rendered, /fixture-secret-123/);
  assert.doesNotMatch(rendered, /fixture-key-789/);
  assert.doesNotMatch(rendered, /fixture quoted secret/);
  assert.doesNotMatch(rendered, /escaped/);
  assert.doesNotMatch(rendered, /direct-passwd/);
  assert.match(rendered, /\[REDACTED:sensitive field\]/);
  assert.match(rendered, /\[REDACTED:authorization header\]/);
});

test("duplicate and malformed host events are harmless", async () => {
  const { adapter, writes } = adapterWith();
  await adapter.event({ event: fixture[1] });
  await adapter.event({ event: fixture[1] });
  await adapter.event({ event: { type: "message.updated", message: { role: "user" } } });
  await adapter.event({ event: fixture[2] });
  await adapter.event({ event: fixture[4] });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(JSON.parse(writes[0].body).events.length, 1);
});

test("streaming and out-of-order message parts retain the final redacted text", async () => {
  const { adapter, writes } = adapterWith();
  await adapter.event({ event: fixture[0] });
  await adapter.event({ event: { type: "message.part.updated", properties: { part: { id: "part-stream", sessionID: "ses-fixture-01", messageID: "msg-stream", type: "text", text: "draft" } } } });
  await adapter.event({ event: { type: "message.updated", properties: { info: { id: "msg-stream", sessionID: "ses-fixture-01", role: "assistant" } } } });
  await adapter.event({ event: { type: "message.part.updated", properties: { part: { id: "part-stream", sessionID: "ses-fixture-01", messageID: "msg-stream", type: "text" }, delta: " final response" } } });
  await adapter.event({ event: fixture[4] });
  await new Promise((resolve) => setImmediate(resolve));
  const record = JSON.parse(writes[0].body);
  assert.equal(record.events.length, 1);
  assert.equal(record.events[0].output.message, "draft final response");
});

test("file edits use the latest observed session when OpenCode omits a session ID", async () => {
  const { adapter, writes } = adapterWith();
  await adapter.event({ event: { type: "session.created", properties: { info: { id: "ses-a", projectID: "project-a" } } } });
  await adapter.event({ event: { type: "session.created", properties: { info: { id: "ses-b", projectID: "project-b" } } } });
  await adapter.event({ event: { type: "file.edited", properties: { file: "src/latest.py" } } });
  await adapter.event({ event: { type: "session.idle", properties: { sessionID: "ses-b" } } });
  await new Promise((resolve) => setImmediate(resolve));
  const record = JSON.parse(writes[0].body);
  assert.equal(record.session_id, "ses-b");
  assert.equal(record.events[0].input.path, "src/latest.py");
});

test("repeated edits to one path receive distinct event IDs", async () => {
  const { adapter, writes } = adapterWith();
  await adapter.event({ event: fixture[0] });
  await adapter.event({ event: fixture[3] });
  await adapter.event({ event: fixture[3] });
  await adapter.event({ event: fixture[4] });
  await new Promise((resolve) => setImmediate(resolve));
  const record = JSON.parse(writes[0].body);
  assert.equal(record.events.length, 2);
  assert.notEqual(record.events[0].id, record.events[1].id);
});

test("environment secret values are removed before the outbox is written", async () => {
  const writes = [];
  const adapter = createAdapter({}, {
    root: "runtime",
    env: { AXEL_CAPTURE_SECRET: "Qz7" },
    ensureDirectory: async () => {},
    writeFile: async (path, body) => { writes.push({ path, body }); },
    syncFile: async () => {},
    replaceFile: async (temporary, target) => {
      const entry = writes.find((item) => item.path === temporary);
      entry.path = target;
    },
    runEngine: async () => true,
  });
  await adapter.event({ event: fixture[0] });
  await adapter.event({ event: fixture[1] });
  await adapter.event({ event: { type: "message.part.updated", properties: { part: { id: "part-secret", sessionID: "ses-fixture-01", messageID: "msg-01", type: "text", text: "value=Qz7" } } } });
  await adapter.event({ event: fixture[4] });
  await new Promise((resolve) => setImmediate(resolve));
  assert.doesNotMatch(writes[0].body, /Qz7/);
  assert.match(writes[0].body, /\[REDACTED:environment secret\]/);
});

test("oversized event payloads are compacted below the engine line limit", async () => {
  const { adapter, writes } = adapterWith();
  const args = Object.fromEntries(Array.from({ length: 100 }, (_, index) => [`field-${index}`, "x".repeat(2048)]));
  await adapter["tool.execute.before"]({ sessionID: "ses-large", tool: "bash", callID: "call-large" }, { args });
  await adapter["tool.execute.before"]({ sessionID: "ses-large", tool: "bash", callID: "call-large-2" }, { args });
  await adapter.event({ event: { type: "session.idle", properties: { sessionID: "ses-large" } } });
  await new Promise((resolve) => setImmediate(resolve));
  const record = JSON.parse(writes[0].body);
  assert.equal(record.metadata.capture_truncated, "event payloads reduced to fit envelope size limit");
  assert.ok(writes[0].body.length < 240001);
});

test("spool or engine failure never rejects an OpenCode hook", async () => {
  const { adapter } = adapterWith({ runEngine: async () => { throw new Error("unavailable"); } });
  await adapter.event({ event: fixture[1] });
  await assert.doesNotReject(adapter.event({ event: fixture[3] }));

  const unavailableSpool = createAdapter({}, {
    root: "runtime",
    ensureDirectory: async () => {},
    writeFile: async () => { throw new Error("spool unavailable"); },
    runEngine: async () => true,
  });
  await unavailableSpool.event({ event: fixture[1] });
  await unavailableSpool.event({ event: fixture[2] });
  await assert.doesNotReject(unavailableSpool.event({ event: fixture[4] }));
});

test("a failed spool write restores the detached segment for a later retry", async () => {
  let shouldFail = true;
  const writes = [];
  const adapter = createAdapter({}, {
    root: "runtime",
    ensureDirectory: async () => {},
    writeFile: async (path, body) => {
      if (shouldFail) throw new Error("spool unavailable");
      writes.push({ path, body });
    },
    syncFile: async () => {},
    replaceFile: async (temporary, target) => {
      const entry = writes.find((item) => item.path === temporary);
      if (!entry) throw new Error("temporary spool file was not written");
      entry.path = target;
    },
    runEngine: async () => true,
  });
  await adapter.event({ event: fixture[1] });
  await adapter.event({ event: fixture[2] });
  await adapter.event({ event: fixture[4] });
  await new Promise((resolve) => setImmediate(resolve));
  shouldFail = false;
  await adapter.event({ event: fixture[4] });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(writes.length, 1);
  assert.equal(JSON.parse(writes[0].body).events.length, 1);
});

test("default filesystem functions retain the outbox until engine success", async () => {
  const { mkdtemp, readdir, rm } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const root = await mkdtemp(`${tmpdir()}/axel-opencode-`);
  const engineCalls = [];
  try {
    const adapter = createAdapter({}, {
      root,
      runEngine: async (outbox) => {
        engineCalls.push(outbox);
        return false;
      },
    });
    await adapter.event({ event: fixture[0] });
    await adapter.event({ event: fixture[1] });
    await adapter.event({ event: fixture[2] });
    await adapter.event({ event: fixture[4] });
     await waitFor(() => engineCalls.length === 1);
    assert.equal(engineCalls.length, 1);
    assert.equal((await readdir(`${root}/spool/outbox`)).length, 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a successful engine run removes the durable outbox", async () => {
  const { mkdtemp, readdir, rm } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const root = await mkdtemp(`${tmpdir()}/axel-opencode-`);
  try {
    const adapter = createAdapter({}, { root, runEngine: async () => true });
    await adapter.event({ event: fixture[0] });
    await adapter.event({ event: fixture[1] });
    await adapter.event({ event: fixture[2] });
    await adapter.event({ event: fixture[4] });
     await waitFor(async () => {
       try {
         return (await readdir(`${root}/spool/outbox`)).length === 0;
       } catch (error) {
         if (error.code === "ENOENT") return true;
         throw error;
       }
     });
     let remaining = [];
     try {
       remaining = await readdir(`${root}/spool/outbox`);
     } catch (error) {
       if (error.code !== "ENOENT") throw error;
     }
     assert.deepEqual(remaining, []);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
