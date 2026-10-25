import { Timestamp } from 'spacetimedb';
import { Router, SyncResponse, schema, t, table } from 'spacetimedb/server';
import type { HandlerContext } from 'spacetimedb/server';

// ---------------------------------------------------------------------------
// Tables. All private: the only way in or out is the HTTP handlers below.
// A meeting row is created when the listener process starts and closed when it stops.
// ---------------------------------------------------------------------------

const meeting = table(
  { name: 'meeting' },
  {
    id: t.string().primaryKey(), // e.g. "meeting-20261024-170501"
    title: t.string(),
    date: t.string(), // YYYY-MM-DD
    started_at: t.timestamp(),
    ended_at: t.option(t.timestamp()), // null while the process is still running
    summary: t.string(),
    key_points: t.array(t.string()),
    decisions: t.array(t.string()),
    notes: t.string(),
    status: t.string(), // live | complete
    created_at: t.timestamp(),
    updated_at: t.timestamp(),
  },
);

// Every finished speech turn heard in the meeting.
const transcriptSegment = table(
  {
    name: 'transcript_segment',
    indexes: [{ accessor: 'by_meeting', algorithm: 'btree', columns: ['meeting_id'] }],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    meeting_id: t.string(),
    text: t.string(),
    at: t.timestamp(),
  },
);

// Every "Hey Argo" command, saved when it is heard (before it is executed).
const command = table(
  {
    name: 'command',
    indexes: [{ accessor: 'by_meeting', algorithm: 'btree', columns: ['meeting_id'] }],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    meeting_id: t.string(),
    utterance_id: t.string(),
    transcript: t.string(),
    intent: t.string(),
    confidence: t.f64(),
    outcome: t.string(), // pending | executed | partial | failed | clarify | dry_run | no_op | error
    spoken_reply: t.string(),
    plan: t.string(), // JSON of the Claude plan
    at: t.timestamp(),
  },
);

// One row per Jira operation a command performed.
const agentAction = table(
  {
    name: 'agent_action',
    indexes: [
      { accessor: 'by_meeting', algorithm: 'btree', columns: ['meeting_id'] },
      { accessor: 'by_issue', algorithm: 'btree', columns: ['issue_key'] },
    ],
  },
  {
    id: t.u64().primaryKey().autoInc(),
    meeting_id: t.string(),
    utterance_id: t.string(), // links back to the command row
    op: t.string(), // create_issue, add_comment, ...
    kind: t.string(), // created | commented | transitioned | assigned | moved | updated | deleted | searched
    issue_key: t.string(),
    title: t.string(),
    detail: t.string(),
    status: t.string(), // done | pending | failed | dry_run
    pre_state: t.string(), // JSON snapshot before the change (for rollback)
    request: t.string(), // JSON of the Jira request that was sent
    at: t.timestamp(),
  },
);

// API_KEY is supplied at publish time:  API_KEY=... spacetime publish ...
const spacetimedb = schema(
  { meeting, transcript_segment: transcriptSegment, command, agent_action: agentAction },
  { env: { API_KEY: t.string() } },
);
export default spacetimedb;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type Ctx = HandlerContext<any>;
type Req = { headers: { get(name: string): string | null }; uri: string; json(): any };

const KINDS = ['created', 'commented', 'transitioned', 'assigned', 'moved', 'updated', 'deleted', 'searched'];
const ACTION_STATUSES = ['done', 'pending', 'failed', 'dry_run'];
const MEETING_STATUSES = ['live', 'complete'];
const OUTCOMES = ['pending', 'executed', 'partial', 'failed', 'clarify', 'dry_run', 'no_op', 'error'];

const json = (data: unknown, status = 200) =>
  new SyncResponse(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json' },
  });

const fail = (status: number, error: string) => json({ error }, status);

// HTTP handlers skip SpacetimeDB's own auth, so every route checks a shared secret.
const unauthorized = (ctx: Ctx, req: Req) => {
  const expected = ctx.env.get('API_KEY');
  return !expected || req.headers.get('x-api-key') !== expected;
};

const readBody = (req: Req): any | null => {
  try {
    return req.json();
  } catch {
    return null;
  }
};

// The beta Router has no path parameters, so ids travel in the query string.
const query = (req: Req) => new URL(req.uri, 'http://argo.local').searchParams;

const str = (v: unknown, fallback = '') => (typeof v === 'string' ? v : fallback);
const strList = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
const parseTime = (v: unknown, fallback: Timestamp) =>
  typeof v === 'string' && !Number.isNaN(Date.parse(v)) ? Timestamp.fromDate(new Date(v)) : fallback;

const byTime = (a: any, b: any) => (a.at.microsSinceUnixEpoch < b.at.microsSinceUnixEpoch ? -1 : 1);

const toAction = (a: any) => ({
  id: a.id.toString(),
  meetingId: a.meeting_id,
  utteranceId: a.utterance_id,
  op: a.op,
  kind: a.kind,
  issueKey: a.issue_key,
  title: a.title,
  detail: a.detail,
  status: a.status,
  at: a.at.toISOString(),
});

const toCommand = (c: any) => ({
  id: c.id.toString(),
  utteranceId: c.utterance_id,
  transcript: c.transcript,
  intent: c.intent,
  confidence: c.confidence,
  outcome: c.outcome,
  spokenReply: c.spoken_reply,
  at: c.at.toISOString(),
});

const toMeeting = (m: any, actions: any[], commands: any[], now: Timestamp) => {
  const end = m.ended_at ?? now;
  const durationSec = Math.max(0, Number((BigInt(end.microsSinceUnixEpoch) - BigInt(m.started_at.microsSinceUnixEpoch)) / 1_000_000n));
  return {
    id: m.id,
    title: m.title,
    date: m.date,
    startedAt: m.started_at.toISOString(),
    endedAt: m.ended_at ? m.ended_at.toISOString() : null,
    durationSec,
    durationMin: Math.round(durationSec / 60),
    summary: m.summary,
    keyPoints: m.key_points,
    decisions: m.decisions,
    notes: m.notes,
    status: m.status,
    updatedAt: m.updated_at.toISOString(),
    actions: actions.sort(byTime).map(toAction),
    commands: commands.sort(byTime).map(toCommand),
  };
};

// ---------------------------------------------------------------------------
// Read endpoints
// ---------------------------------------------------------------------------

// GET /meetings -> Meeting[] (newest first)
export const listMeetings = spacetimedb.httpHandler((ctx, req) => {
  if (unauthorized(ctx, req)) return fail(401, 'Missing or invalid x-api-key');
  const out = ctx.withTx((tx) =>
    [...tx.db.meeting.iter()]
      .sort((a, b) => (a.started_at.microsSinceUnixEpoch < b.started_at.microsSinceUnixEpoch ? 1 : -1))
      .map((m) =>
        toMeeting(m, [...tx.db.agent_action.by_meeting.filter(m.id)], [...tx.db.command.by_meeting.filter(m.id)], ctx.timestamp),
      ),
  );
  return json(out);
});

// GET /meeting?id=<id> -> Meeting
export const getMeeting = spacetimedb.httpHandler((ctx, req) => {
  if (unauthorized(ctx, req)) return fail(401, 'Missing or invalid x-api-key');
  const id = query(req).get('id');
  if (!id) return fail(400, 'Missing id');
  const out = ctx.withTx((tx) => {
    const m = tx.db.meeting.id.find(id);
    return m
      ? toMeeting(m, [...tx.db.agent_action.by_meeting.filter(id)], [...tx.db.command.by_meeting.filter(id)], ctx.timestamp)
      : null;
  });
  return out ? json(out) : fail(404, 'Meeting not found');
});

// GET /transcript?id=<meeting id> -> { at, text }[]
export const getTranscript = spacetimedb.httpHandler((ctx, req) => {
  if (unauthorized(ctx, req)) return fail(401, 'Missing or invalid x-api-key');
  const id = query(req).get('id');
  if (!id) return fail(400, 'Missing id');
  const out = ctx.withTx((tx) =>
    [...tx.db.transcript_segment.by_meeting.filter(id)].sort(byTime).map((s) => ({ at: s.at.toISOString(), text: s.text })),
  );
  return json(out);
});

// GET /issue-actions?key=KAN-1 -> AgentAction[] (for the Jira issue panel)
export const issueActions = spacetimedb.httpHandler((ctx, req) => {
  if (unauthorized(ctx, req)) return fail(401, 'Missing or invalid x-api-key');
  const key = query(req).get('key');
  if (!key) return fail(400, 'Missing key');
  const out = ctx.withTx((tx) => [...tx.db.agent_action.by_issue.filter(key)].sort(byTime).map(toAction));
  return json(out);
});

// ---------------------------------------------------------------------------
// Write endpoints (used by the agent-server)
// ---------------------------------------------------------------------------

// POST /meetings -> create or update a meeting by id. Called at process start
// ({id, title, status: "live"}) and again at the end ({id, status: "complete", endedAt, summary, ...}).
export const saveMeeting = spacetimedb.httpHandler((ctx, req) => {
  if (unauthorized(ctx, req)) return fail(401, 'Missing or invalid x-api-key');
  const body = readBody(req);
  const id = str(body?.id).trim();
  if (!id) return fail(400, 'id is required');
  const status = str(body.status, 'live');
  if (!MEETING_STATUSES.includes(status)) return fail(400, `status must be one of ${MEETING_STATUSES.join(', ')}`);

  const out = ctx.withTx((tx) => {
    const existing = tx.db.meeting.id.find(id);
    const title = str(body.title).trim() || existing?.title;
    if (!title) return { ok: false as const, error: 'title is required for a new meeting' };
    const now = ctx.timestamp;
    const startedAt = existing?.started_at ?? parseTime(body.startedAt, now);
    const row = {
      id,
      title,
      date: existing?.date ?? startedAt.toISOString().slice(0, 10),
      started_at: startedAt,
      ended_at: body.endedAt ? parseTime(body.endedAt, now) : (existing?.ended_at ?? undefined),
      summary: str(body.summary, existing?.summary ?? ''),
      key_points: body.keyPoints === undefined ? (existing?.key_points ?? []) : strList(body.keyPoints),
      decisions: body.decisions === undefined ? (existing?.decisions ?? []) : strList(body.decisions),
      notes: str(body.notes, existing?.notes ?? ''),
      status,
      created_at: existing?.created_at ?? now,
      updated_at: now,
    };
    if (existing) tx.db.meeting.id.update(row);
    else tx.db.meeting.insert(row);
    return { ok: true as const, id, created: !existing };
  });
  if (!out.ok) return fail(400, out.error);
  return json(out, out.created ? 201 : 200);
});

// POST /segments -> {meetingId, text, at?}
export const saveSegment = spacetimedb.httpHandler((ctx, req) => {
  if (unauthorized(ctx, req)) return fail(401, 'Missing or invalid x-api-key');
  const body = readBody(req);
  const meetingId = str(body?.meetingId).trim();
  const text = str(body?.text).trim();
  if (!meetingId || !text) return fail(400, 'meetingId and text are required');
  const ok = ctx.withTx((tx) => {
    if (!tx.db.meeting.id.find(meetingId)) return false;
    tx.db.transcript_segment.insert({ id: 0n, meeting_id: meetingId, text, at: parseTime(body.at, ctx.timestamp) });
    return true;
  });
  return ok ? json({ ok: true }, 201) : fail(404, 'Meeting not found');
});

// POST /commands -> {meetingId, utteranceId, transcript, intent?, confidence?, outcome?, spokenReply?, plan?, at?}
export const saveCommand = spacetimedb.httpHandler((ctx, req) => {
  if (unauthorized(ctx, req)) return fail(401, 'Missing or invalid x-api-key');
  const body = readBody(req);
  const meetingId = str(body?.meetingId).trim();
  const utteranceId = str(body?.utteranceId).trim();
  if (!meetingId || !utteranceId) return fail(400, 'meetingId and utteranceId are required');
  const outcome = str(body.outcome, 'pending');
  if (!OUTCOMES.includes(outcome)) return fail(400, `outcome must be one of ${OUTCOMES.join(', ')}`);

  const out = ctx.withTx((tx) => {
    if (!tx.db.meeting.id.find(meetingId)) return null;
    // Upsert by (meeting, utterance): saved as "pending" when heard, then updated with the outcome.
    const existing = [...tx.db.command.by_meeting.filter(meetingId)].find((c) => c.utterance_id === utteranceId);
    const row = {
      id: existing?.id ?? 0n,
      meeting_id: meetingId,
      utterance_id: utteranceId,
      transcript: str(body.transcript, existing?.transcript ?? ''),
      intent: str(body.intent, existing?.intent ?? ''),
      confidence: typeof body.confidence === 'number' ? body.confidence : (existing?.confidence ?? 0),
      outcome,
      spoken_reply: str(body.spokenReply, existing?.spoken_reply ?? ''),
      plan: body.plan === undefined ? (existing?.plan ?? '') : JSON.stringify(body.plan),
      at: existing?.at ?? parseTime(body.at, ctx.timestamp),
    };
    const saved = existing ? tx.db.command.id.update(row) : tx.db.command.insert(row);
    return toCommand(saved);
  });
  return out ? json(out, 201) : fail(404, 'Meeting not found');
});

// POST /actions -> one Jira operation performed for a command
export const saveAction = spacetimedb.httpHandler((ctx, req) => {
  if (unauthorized(ctx, req)) return fail(401, 'Missing or invalid x-api-key');
  const body = readBody(req);
  const meetingId = str(body?.meetingId).trim();
  const kind = str(body?.kind);
  const status = str(body?.status, 'done');
  if (!meetingId) return fail(400, 'meetingId is required');
  if (!KINDS.includes(kind)) return fail(400, `kind must be one of ${KINDS.join(', ')}`);
  if (!ACTION_STATUSES.includes(status)) return fail(400, `status must be one of ${ACTION_STATUSES.join(', ')}`);

  const out = ctx.withTx((tx) => {
    if (!tx.db.meeting.id.find(meetingId)) return null;
    const row = tx.db.agent_action.insert({
      id: 0n,
      meeting_id: meetingId,
      utterance_id: str(body.utteranceId),
      op: str(body.op),
      kind,
      issue_key: str(body.issueKey),
      title: str(body.title),
      detail: str(body.detail),
      status,
      pre_state: body.preState === undefined ? '' : JSON.stringify(body.preState),
      request: body.request === undefined ? '' : JSON.stringify(body.request),
      at: parseTime(body.at, ctx.timestamp),
    });
    return toAction(row);
  });
  return out ? json(out, 201) : fail(404, 'Meeting not found');
});

export const router = spacetimedb.httpRouter(
  new Router()
    .get('/meetings', listMeetings)
    .post('/meetings', saveMeeting)
    .get('/meeting', getMeeting)
    .get('/transcript', getTranscript)
    .get('/issue-actions', issueActions)
    .post('/segments', saveSegment)
    .post('/commands', saveCommand)
    .post('/actions', saveAction),
);
