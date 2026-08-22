import { mkdir, open as openFile, rename, unlink, writeFile as writeTextFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";

/*
 * Local-only OpenCode capture plugin.
 *
 * OpenCode loads project plugins from .opencode/plugins. This adapter uses the
 * documented generic `event` hook plus tool before/after hooks; it neither
 * modifies hook output nor reads an OpenCode transcript.
 */

const ADAPTER_VERSION = "1";
const MAX_TEXT = 2048;
const SENSITIVE_KEY = /(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|password|passwd|secret|cookie|credential|private[_-]?key|token)/i;
const PATH_KEY = /(?:path|file|cwd|worktree|directory|dir)$/i;
const PRIVATE_KEY = /-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----/gis;
const COOKIE_HEADER = /\b(?:Cookie|Set-Cookie)\s*:\s*[^\r\n]+/gi;
const AUTHORIZATION_HEADER = /\b(?:Proxy-)?Authorization\s*:\s*[^\r\n]+/gi;
const COOKIE_ASSIGNMENT = /\b(?:sessionid|session_id|sid|csrftoken|xsrf[-_]?token|auth[-_]?token|refresh[-_]?token|cookie)\s*=\s*[^\s,;]+/gi;
const TOKEN_PREFIX = /\b(?:sk|gh[pousr]?|github_pat|xox[baprs]|AIza)[_-][A-Za-z0-9._-]{8,}\b|\bAKIA[0-9A-Z]{16}\b/gi;
const SECRET_ASSIGNMENT_NAME = "(?:api[_-]?key|access[_-]?token|authorization|auth|bearer|password|passwd|secret|token|credential|private[_-]?key)";
const ASSIGNED_SECRET = new RegExp(`\\b(?=[A-Za-z0-9_-]*${SECRET_ASSIGNMENT_NAME}[A-Za-z0-9_-]*"?\\s*[:=])[A-Za-z][A-Za-z0-9_-]*"?\\s*[:=]\\s*("(?:\\\\.|[^"\\\\])*"|'(?:\\\\.|[^'\\\\])*'|[^\\s,;]+)`, "gi");
const ASSIGNED_SECRET_KEY = new RegExp(`\\b(?=[A-Za-z0-9_-]*${SECRET_ASSIGNMENT_NAME}[A-Za-z0-9_-]*"?\\s*[:=])[A-Za-z][A-Za-z0-9_-]*"?\\s*[:=]`, "i");
const ABSOLUTE_PATH = /^(?:[A-Za-z]:[\\/]|\\\\|\/)/;
const ABSOLUTE_PATH_TOKEN = /(?:[A-Za-z]:[\\/]|\\\\|\/)[^\s"'<>]+/gi;
const SENSITIVE_PATH = /(?:^|[\\/])(?:\.env(?:\.[^\\/]*)?|\.aws[\\/](?:credentials|config)|auth(?:entication)?\.json|credentials?(?:[\\/]|$)|[^\\/]*\.(?:pem|key))[.,;:)]*$/i;
const MAX_ENVELOPE_CHARS = 240000;
const MAX_EVENTS = 100;

function text(value, fallback = "") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function object(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function stableId(...parts) {
  const source = parts.join("\u001f");
  let hash = 2166136261;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function redactInlineCredentialPaths(value) {
  return value.replace(ABSOLUTE_PATH_TOKEN, (token) => {
    const clean = token.replace(/[.,;:)]}+$/, "");
    const punctuation = token.slice(clean.length);
    return SENSITIVE_PATH.test(clean) ? `[REDACTED:credential path]${punctuation}` : token;
  });
}

function redactString(value, key = "", secretValues = []) {
  if (SENSITIVE_KEY.test(key)) return "[REDACTED:sensitive field]";
  let safe = value;
  for (const secret of secretValues) safe = safe.split(secret).join("[REDACTED:environment secret]");
  safe = safe
    .replace(PRIVATE_KEY, "[REDACTED:private key]")
    .replace(COOKIE_HEADER, "[REDACTED:cookie header]")
    .replace(AUTHORIZATION_HEADER, "[REDACTED:authorization header]")
    .replace(COOKIE_ASSIGNMENT, "[REDACTED:cookie value]")
    .replace(/\bBearer\s+[A-Za-z0-9._~+\-/=]+/gi, "Bearer [REDACTED:token]")
    .replace(TOKEN_PREFIX, "[REDACTED:token]")
    .replace(ASSIGNED_SECRET, "[REDACTED:assigned secret]");
  safe = redactInlineCredentialPaths(safe);
  if (PATH_KEY.test(key)) {
    if (SENSITIVE_PATH.test(safe)) return "[REDACTED:credential path]";
    if (ABSOLUTE_PATH.test(safe)) return `[PATH:${stableId(safe)}]`;
  }
  return safe.length > MAX_TEXT ? `${safe.slice(0, MAX_TEXT)} [TRUNCATED]` : safe;
}

function bounded(value, key = "", depth = 0, secretValues = []) {
  if (depth > 8) return "[TRUNCATED:maximum nesting depth]";
  if (key && SENSITIVE_KEY.test(key)) return "[REDACTED:sensitive field]";
  if (typeof value === "string") return redactString(value, key, secretValues);
  if (typeof value === "number" || typeof value === "boolean" || value === null) return value;
  if (Array.isArray(value)) {
    const items = value.length > 100
      ? [...value.slice(0, 99), "[TRUNCATED:maximum collection items]"]
      : value;
    return items.map((item) => bounded(item, key, depth + 1, secretValues));
  }
  if (value && typeof value === "object") {
    const headerName = value.name ?? value.key ?? value.header;
    const sensitiveHeader = typeof headerName === "string" && SENSITIVE_KEY.test(headerName);
    const entries = Object.entries(value);
    const limitedEntries = entries.length > 100 ? entries.slice(0, 99) : entries;
    const result = Object.fromEntries(limitedEntries.map(([name, item]) => [
      SENSITIVE_KEY.test(name) || ASSIGNED_SECRET_KEY.test(name)
        ? "[REDACTED:key]"
        : name.length > MAX_TEXT ? `${name.slice(0, MAX_TEXT)} [TRUNCATED:key]` : name,
      sensitiveHeader && /^(?:value|values|content|data)$/i.test(name)
        ? "[REDACTED:sensitive field]"
        : bounded(item, name, depth + 1, secretValues),
    ]));
    if (entries.length > 100) result["[TRUNCATED:collection]"] = "[TRUNCATED:maximum collection items]";
    return result;
  }
  return "[REDACTED:unsupported value]";
}

function hostData(event) {
  const root = object(event);
  const properties = object(root.properties);
  const info = object(properties.info);
  const message = object(root.message || properties.message);
  const part = object(root.part || properties.part);
  const session = object(root.session || properties.session || info.session);
  return { root, properties, info, message, part, session };
}

function sessionId(value) {
  const { root, properties, info, message, part, session } = hostData(value);
  const type = text(root.type);
  const sessionInfoId = type.startsWith("session.") ? info.id : "";
  return text(
    root.sessionID || root.session_id || properties.sessionID || properties.session_id ||
    info.sessionID || info.session_id || message.sessionID || message.session_id ||
    part.sessionID || part.session_id || session.id || sessionInfoId,
  );
}

function projectId(value) {
  const { root, properties, info } = hostData(value);
  const project = object(root.project || properties.project);
  return text(
    project.id || project.projectID || info.projectID || info.project_id ||
    root.projectID || root.project_id || properties.projectID || properties.project_id,
    "project-unknown",
  );
}

function messageEvent(event, currentSession) {
  const { root, info, message } = hostData(event);
  const details = Object.keys(message).length ? message : info;
  const role = text(details.role || root.role);
  const content = details.content ?? details.text ?? root.content ?? root.text;
  if (!sessionId(event) || !content || !["user", "assistant"].includes(role)) return null;
  return {
    id: `opencode-${stableId(currentSession, "message", details.id || JSON.stringify(content))}`,
    type: role === "user" ? "user_prompt" : "assistant_message",
    input: role === "user" ? { prompt: content } : undefined,
    output: role === "assistant" ? { message: content } : undefined,
    created_at: new Date().toISOString(),
  };
}

function messageRole(event) {
  const { root, info, message } = hostData(event);
  const details = Object.keys(message).length ? message : info;
  return { id: text(details.id), role: text(details.role || root.role) };
}

function messagePartRecord(event, segment, secretValues) {
  const { root, properties, part } = hostData(event);
  if (part.type && part.type !== "text") return null;
  const partId = text(part.id, stableId(part.messageID || part.message_id, part.text || part.delta || root.text));
  const previous = segment.message_parts.get(partId);
  const delta = typeof properties.delta === "string" ? properties.delta : (typeof part.delta === "string" ? part.delta : "");
  const rawContent = typeof part.text === "string" && part.text
    ? part.text
    : `${previous?.content || ""}${delta || (typeof part.content === "string" ? part.content : root.text || "")}`;
  const content = typeof rawContent === "string" ? bounded(rawContent, "text", 0, secretValues) : "";
  const messageId = text(part.messageID || part.message_id, "message");
  const record = {
    id: partId,
    message_id: messageId,
    role: text(part.role || segment.message_roles.get(messageId)),
    content,
  };
  segment.message_parts.set(partId, record);
  return record;
}

function messagePartEnvelope(currentSession, record) {
  const { content, role } = record;
  if (typeof content !== "string" || !content.trim() || !["user", "assistant"].includes(role)) return null;
  return {
    id: `opencode-${stableId(currentSession, "message-part", record.id)}`,
    type: role === "user" ? "user_prompt" : "assistant_message",
    input: role === "user" ? { prompt: content } : undefined,
    output: role === "assistant" ? { message: content } : undefined,
    created_at: new Date().toISOString(),
  };
}

function fileEvent(event, currentSession) {
  const { root, properties, info } = hostData(event);
  const rawFile = root.file ?? properties.file ?? info.file;
  const file = typeof rawFile === "string" ? rawFile : object(rawFile);
  const path = typeof file === "string" ? file : file.path || file.name || root.path || properties.path;
  if (!currentSession || !path) return null;
  return {
    id: `opencode-${stableId(currentSession, "file", path, randomUUID())}`,
    type: "file_change",
    input: { path },
    status: "edited",
    created_at: new Date().toISOString(),
  };
}

export function createAdapter(context = {}, runtime = {}) {
  const segments = new Map();
  const flushing = new Set();
  const pendingFlushes = new Map();
  let lastSessionId = "";
  const bun = runtime.bun || globalThis.Bun;
  const environment = runtime.env || bun?.env || (typeof process !== "undefined" ? process.env : {});
  const environmentSecrets = Object.entries(environment)
    .filter(([key, value]) => SENSITIVE_KEY.test(key) && typeof value === "string" && value.length > 0)
    .map(([, value]) => value)
    .sort((left, right) => right.length - left.length);
  const writeFile = runtime.writeFile || ((path, body) => writeTextFile(path, body, "utf8"));
  const ensureDirectory = runtime.ensureDirectory || ((path) => mkdir(path, { recursive: true }));
  const removeFile = runtime.removeFile || ((path) => unlink(path));
  const replaceFile = runtime.replaceFile || ((temporary, target) => rename(temporary, target));
  const syncFile = runtime.syncFile || (async (path) => {
    const handle = await openFile(path, "r+");
    try {
      await handle.sync();
    } finally {
      await handle.close();
    }
  });
  const runEngine = runtime.runEngine || (async (outbox, root) => {
    if (!bun?.spawn) return false;
    const command = text(environment.AXEL_IMPROVE_COMMAND, "axel-improve").split(/\s+/);
    const child = bun.spawn([...command, "ingest", "--root", root, "--input", outbox], { stdout: "ignore", stderr: "ignore" });
    const timer = setTimeout(() => child.kill(), 1000);
    const code = await child.exited;
    clearTimeout(timer);
    return code === 0;
  });
  const root = text(runtime.root || environment.AXEL_IMPROVE_ROOT, ".axel-improve");

  function segmentFor(id, source) {
    if (!segments.has(id)) {
      segments.set(id, {
        session_id: id,
        project_id: projectId(source),
        task: "Host task was not captured",
        events: [],
        ids: new Set(),
        message_roles: new Map(),
        message_parts: new Map(),
        started_at: new Date().toISOString(),
      });
    }
    return segments.get(id);
  }

  function sessionForEvent(event, name) {
    const direct = sessionId(event);
    if (direct) {
      lastSessionId = direct;
      return direct;
    }
    // OpenCode's file.edited event has no session ID; use the latest observed
    // session rather than silently losing edits when sessions overlap.
    if (name === "file.edited" && lastSessionId) return lastSessionId;
    if (name === "file.edited" && segments.size === 1) return segments.keys().next().value;
    return "";
  }

  function add(segment, event, replace = false) {
    if (!event) return;
    if (segment.ids.has(event.id)) {
      if (!replace) return;
      const index = segment.events.findIndex((item) => item.id === event.id);
      if (index < 0) return;
      const safe = bounded(event, "", 0, environmentSecrets);
      segment.events[index] = safe;
      if (safe.type === "user_prompt") segment.task = text(safe.input?.prompt, segment.task);
      return;
    }
    const safe = bounded(event, "", 0, environmentSecrets);
    segment.ids.add(safe.id);
    segment.events.push(safe);
    if (safe.type === "user_prompt") segment.task = text(safe.input?.prompt, segment.task);
  }

  function restore(id, segment) {
    const current = segments.get(id);
    if (!current) {
      segments.set(id, segment);
      return;
    }
    for (const event of current.events) add(segment, event);
    for (const [messageId, role] of current.message_roles) segment.message_roles.set(messageId, role);
    for (const [partId, part] of current.message_parts) segment.message_parts.set(partId, part);
    if (segment.task === "Host task was not captured") segment.task = current.task;
    segments.set(id, segment);
  }

  function fitEnvelope(envelope) {
    const encoded = JSON.stringify(envelope);
    if (encoded.length <= MAX_ENVELOPE_CHARS) return { envelope, encoded };
    const compacted = {
      ...envelope,
      events: envelope.events.map((event) => ({
        id: event.id,
        type: event.type,
        tool_name: event.tool_name,
        input: "[TRUNCATED:envelope size]",
        output: "[TRUNCATED:envelope size]",
        status: event.status,
        created_at: event.created_at,
      })),
      metadata: {
        ...envelope.metadata,
        capture_truncated: "event payloads reduced to fit envelope size limit",
      },
    };
    const compactedEncoded = JSON.stringify(compacted);
    if (compactedEncoded.length <= MAX_ENVELOPE_CHARS) return { envelope: compacted, encoded: compactedEncoded };
    const minimal = {
      ...compacted,
      task: "[TRUNCATED:envelope size]",
      events: compacted.events.map((event) => ({
        id: event.id,
        type: event.type,
        tool_name: event.tool_name,
        status: event.status,
        created_at: event.created_at,
        })),
    };
    const minimalEncoded = JSON.stringify(minimal);
    return minimalEncoded.length <= MAX_ENVELOPE_CHARS
      ? { envelope: minimal, encoded: minimalEncoded }
      : null;
  }

  async function flush(id, reason) {
    if (flushing.has(id)) {
      pendingFlushes.set(id, reason);
      return;
    }
    const segment = segments.get(id);
    if (!segment) return;
    segments.delete(id);
    if (!segment.events.length) return;
    flushing.add(id);
    let spooled = false;
    try {
      const eventWasTruncated = segment.events.length > MAX_EVENTS;
      const capturedEvents = eventWasTruncated
        ? [...segment.events.slice(0, MAX_EVENTS - 1), segment.events[segment.events.length - 1]]
        : segment.events;
      const rawEnvelope = bounded({
        schema_version: 1,
        id: `opencode-${stableId(id, segment.started_at, ...capturedEvents.map((event) => event.id))}`,
        host: "opencode",
        host_version: text(context.version || context.client?.version || environment.OPENCODE_VERSION, "unknown"),
        project_id: segment.project_id,
        session_id: id,
        task: segment.task,
        events: capturedEvents,
        outcome: { status: "unevaluated", summary: `OpenCode capture segment ended at ${reason}.`, evaluation: { status: "unevaluated", validators: [] } },
        metadata: {
          adapter: "opencode-plugin",
          adapter_version: ADAPTER_VERSION,
          ...(eventWasTruncated ? { capture_truncated: "events reduced to preserve the terminal event" } : {}),
        },
        created_at: segment.started_at,
      }, "", 0, environmentSecrets);
      const fitted = fitEnvelope(rawEnvelope);
      if (!fitted) {
        restore(id, segment);
        return;
      }
      const { envelope, encoded } = fitted;
      const outbox = `${root}/spool/outbox/${envelope.id}.jsonl`;
      const temporary = `${outbox}.tmp`;
      try {
        await ensureDirectory(`${root}/spool/outbox`);
        await writeFile(temporary, `${encoded}\n`);
        await syncFile(temporary);
        await replaceFile(temporary, outbox);
        spooled = true;
      } catch (_) {
        try {
          await removeFile(temporary);
        } catch (_) {}
        restore(id, segment);
        return;
      }
      try {
        if (await runEngine(outbox, root)) await removeFile(outbox);
      } catch (_) {
        // The durable outbox remains available for a later retry.
      }
    } finally {
      flushing.delete(id);
      const pendingReason = pendingFlushes.get(id);
      pendingFlushes.delete(id);
      if (spooled && pendingReason && segments.get(id)?.events.length) void flush(id, pendingReason);
    }
  }

  return {
    event: async ({ event }) => {
      try {
        const name = text(event?.type);
        const id = sessionForEvent(event, name);
        if (!id) return;
        const segment = segmentFor(id, event);
        if (name === "message.updated") {
          const message = messageRole(event);
          if (message.id && message.role) segment.message_roles.set(message.id, message.role);
          add(segment, messageEvent(event, id));
          if (message.id && message.role) {
            for (const part of segment.message_parts.values()) {
              if (part.message_id !== message.id) continue;
              part.role = message.role;
              add(segment, messagePartEnvelope(id, part), true);
            }
          }
        }
        if (name === "message.part.updated") {
          const part = messagePartRecord(event, segment, environmentSecrets);
          add(segment, part && messagePartEnvelope(id, part), true);
        }
        if (name === "file.edited") add(segment, fileEvent(event, id));
        if (name === "session.idle" || name === "session.deleted" || name === "session.error") void flush(id, name);
      } catch (_) {
        // The documented event hook is observational only.
      }
    },
    "tool.execute.before": async (input, output) => {
      try {
        const id = sessionId(input);
        if (!id) return;
        const args = output?.args ?? input.args;
        const callId = text(input.callID || input.call_id, stableId(input.tool, JSON.stringify(args)));
        add(segmentFor(id, input), { id: `opencode-${stableId(id, callId, "call")}`, type: "tool_call", tool_name: text(input.tool, "unknown"), input: args, status: "started", created_at: new Date().toISOString() });
      } catch (_) {}
    },
    "tool.execute.after": async (input, output) => {
      try {
        const id = sessionId(input);
        if (!id) return;
        const callId = text(input.callID || input.call_id, stableId(input.tool, JSON.stringify(input.args)));
        add(segmentFor(id, input), { id: `opencode-${stableId(id, callId, "result")}`, type: "tool_result", tool_name: text(input.tool, "unknown"), input: input.args, output, status: "completed", created_at: new Date().toISOString() });
      } catch (_) {}
    },
  };
}

// OpenCode discovers exported plugin functions from project plugin directories.
export const AxelImproveCapture = async (context) => createAdapter(context);
