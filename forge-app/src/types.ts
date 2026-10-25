// Shapes returned by the meetings API (see ../spacetimedb/src/index.ts, toMeeting/toAction/toCommand).

export type ActionKind =
  | 'created'
  | 'commented'
  | 'transitioned'
  | 'assigned'
  | 'moved'
  | 'updated'
  | 'deleted'
  | 'searched';

export interface AgentAction {
  id: string;
  meetingId: string;
  utteranceId: string;
  op: string;
  kind: ActionKind;
  issueKey: string;
  title: string;
  detail: string;
  status: 'done' | 'pending' | 'failed' | 'dry_run';
  at: string; // ISO timestamp
}

export interface Command {
  id: string;
  utteranceId: string;
  transcript: string;
  intent: string;
  confidence: number;
  outcome: 'pending' | 'executed' | 'partial' | 'failed' | 'clarify' | 'dry_run' | 'no_op' | 'error';
  spokenReply: string;
  at: string;
}

export interface TranscriptSegment {
  at: string; // ISO timestamp
  text: string;
}

export interface Meeting {
  id: string;
  title: string;
  date: string; // YYYY-MM-DD
  startedAt: string;
  endedAt: string | null; // null while the listener is still running
  durationSec: number;
  durationMin: number;
  summary: string;
  keyPoints: string[];
  decisions: string[];
  notes: string;
  status: 'live' | 'complete';
  updatedAt: string;
  actions: AgentAction[];
  commands: Command[];
}
