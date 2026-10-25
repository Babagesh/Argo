import React, { useEffect, useMemo, useState } from 'react';
import {
  Box,
  Button,
  DynamicTable,
  Heading,
  Inline,
  Lozenge,
  SectionMessage,
  Select,
  Spinner,
  Stack,
  Strong,
  Tab,
  TabList,
  TabPanel,
  Tabs,
  Tag,
  TagGroup,
  Text,
  Textfield,
  xcss,
} from '@forge/react';
import { invoke, view } from '@forge/bridge';
import type { Command, Meeting, TranscriptSegment } from '../types';

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

const statusAppearance = { done: 'success', pending: 'inprogress', failed: 'removed', dry_run: 'default' } as const;
const outcomeAppearance: Record<Command['outcome'], 'success' | 'inprogress' | 'removed' | 'moved' | 'default'> = {
  executed: 'success',
  pending: 'inprogress',
  partial: 'moved',
  failed: 'removed',
  error: 'removed',
  clarify: 'moved',
  dry_run: 'default',
  no_op: 'default',
};

const formatDuration = (sec: number) => {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m === 0 ? `${s}s` : `${m}m ${s}s`;
};
const formatClock = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' });
const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
// "02:15" = how far into the meeting something happened
const intoMeeting = (iso: string, startedAt: string) => {
  const sec = Math.max(0, Math.round((new Date(iso).getTime() - new Date(startedAt).getTime()) / 1000));
  return `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`;
};
const cell = (content: string, width?: number) => ({ key: content, content, width });

// ---------------------------------------------------------------------------
// Building blocks (all Atlassian components + design tokens, no custom CSS)
// ---------------------------------------------------------------------------

// width: 100% makes cards in a Row share the space equally (this Forge version has no Grid).
const cardStyles = xcss({
  backgroundColor: 'elevation.surface.raised',
  borderColor: 'color.border',
  borderStyle: 'solid',
  borderWidth: 'border.width',
  borderRadius: 'radius.large',
  padding: 'space.300',
  width: '100%',
});

const highlightStyles = xcss({
  backgroundColor: 'color.background.information',
  borderColor: 'color.border.information',
  borderStyle: 'solid',
  borderWidth: 'border.width',
  borderRadius: 'radius.large',
  padding: 'space.300',
  width: '100%',
});

// Equal-width cards side by side.
const Row = ({ children, space = 'space.200' }: { children: React.ReactNode; space?: 'space.200' | 'space.300' }) => (
  <Inline space={space} alignBlock="stretch">
    {children}
  </Inline>
);

function Card({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <Box xcss={cardStyles}>
      <Stack space="space.200">
        <Stack space="space.050">
          <Heading size="medium">{title}</Heading>
          {subtitle && <Text color="color.text.subtle">{subtitle}</Text>}
        </Stack>
        {children}
      </Stack>
    </Box>
  );
}

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Box xcss={cardStyles}>
      <Stack space="space.050">
        <Text size="small" weight="semibold" color="color.text.subtle">{label.toUpperCase()}</Text>
        <Heading size="xlarge">{value}</Heading>
        {hint && <Text size="small" color="color.text.subtle">{hint}</Text>}
      </Stack>
    </Box>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <Stack space="space.025">
      <Text size="small" weight="semibold" color="color.text.subtle">{label.toUpperCase()}</Text>
      <Text>{value}</Text>
    </Stack>
  );
}

// Wall-clock time over how far into the meeting it happened.
function TimeStamp({ at, startedAt, suffix = '' }: { at: string; startedAt: string; suffix?: string }) {
  return (
    <Stack space="space.025">
      <Text weight="medium">{formatClock(at)}</Text>
      <Text size="small" color="color.text.subtle">{`+${intoMeeting(at, startedAt)}${suffix}`}</Text>
    </Stack>
  );
}

const StatusLozenge = ({ status }: { status: Meeting['status'] }) => (
  <Lozenge appearance={status === 'live' ? 'inprogress' : 'success'} isBold>
    {status === 'live' ? 'Live' : 'Complete'}
  </Lozenge>
);

// ---------------------------------------------------------------------------
// Meeting list
// ---------------------------------------------------------------------------

const SORT_OPTIONS = [
  { label: 'Newest first', value: 'newest' },
  { label: 'Most Jira changes', value: 'actions' },
  { label: 'Most commands', value: 'commands' },
];

function MeetingList({ meetings, onOpen }: { meetings: Meeting[]; onOpen: (id: string) => void }) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<'all' | 'week' | 'live'>('all');
  const [sort, setSort] = useState(SORT_OPTIONS[0]);

  const visible = useMemo(
    () =>
      meetings
        .filter((m) => m.title.toLowerCase().includes(query.toLowerCase()))
        .filter((m) => filter !== 'week' || Date.now() - new Date(m.startedAt).getTime() <= 7 * 864e5)
        .filter((m) => filter !== 'live' || m.status === 'live')
        .sort((a, b) =>
          sort.value === 'actions'
            ? b.actions.length - a.actions.length
            : sort.value === 'commands'
              ? b.commands.length - a.commands.length
              : b.startedAt.localeCompare(a.startedAt),
        ),
    [meetings, query, filter, sort],
  );

  const totalCommands = meetings.reduce((n, m) => n + m.commands.length, 0);
  const totalActions = meetings.reduce((n, m) => n + m.actions.length, 0);
  const liveNow = meetings.filter((m) => m.status === 'live').length;

  const head = {
    cells: [
      cell('Meeting', 22),
      cell('Started', 16),
      cell('Length', 9),
      cell('Status', 10),
      cell('Commands', 9),
      cell('Jira changes', 10),
      cell('Summary', 24),
    ],
  };
  const rows = visible.map((m) => ({
    key: m.id,
    cells: [
      { key: 'title', content: <Button appearance="subtle" onClick={() => onOpen(m.id)}>{m.title}</Button> },
      {
        key: 'started',
        content: (
          <Stack space="space.025">
            <Text weight="medium">{formatDate(m.startedAt)}</Text>
            <Text size="small" color="color.text.subtle">{formatClock(m.startedAt)}</Text>
          </Stack>
        ),
      },
      { key: 'length', content: formatDuration(m.durationSec) },
      { key: 'status', content: <StatusLozenge status={m.status} /> },
      { key: 'commands', content: String(m.commands.length) },
      { key: 'actions', content: String(m.actions.length) },
      {
        key: 'summary',
        content: (
          <Text maxLines={2} color="color.text.subtle">
            {m.summary || (m.status === 'live' ? 'In progress…' : 'No summary')}
          </Text>
        ),
      },
    ],
  }));

  return (
    <Stack space="space.300">
      <Stack space="space.075">
        <Heading size="xxlarge">Meeting reports</Heading>
        <Text size="large" color="color.text.subtle">
          Everything Argo heard, did and summarized in your meetings.
        </Text>
      </Stack>

      <Row>
        <StatCard label="Meetings" value={String(meetings.length)} />
        <StatCard label="Commands heard" value={String(totalCommands)} />
        <StatCard label="Jira changes" value={String(totalActions)} />
        <StatCard label="Live now" value={String(liveNow)} hint={liveNow > 0 ? 'Updating every few seconds' : undefined} />
      </Row>

      <Card title="All meetings">
        <Inline space="space.200" alignBlock="end">
          <Textfield placeholder="Search meetings" value={query} onChange={(e) => setQuery(e.target.value)} />
          <Select options={SORT_OPTIONS} value={sort} onChange={(o) => o && setSort(o)} />
        </Inline>
        <Tabs id="meeting-filter" onChange={(i) => setFilter((['all', 'week', 'live'] as const)[i])}>
          <TabList>
            <Tab>All</Tab>
            <Tab>This week</Tab>
            <Tab>Live</Tab>
          </TabList>
          <TabPanel>{null}</TabPanel>
          <TabPanel>{null}</TabPanel>
          <TabPanel>{null}</TabPanel>
        </Tabs>
        {visible.length === 0 ? (
          <SectionMessage title={meetings.length === 0 ? 'No meetings yet' : 'No meetings match'} appearance="information">
            <Text>
              {meetings.length === 0
                ? 'Start the Argo listener in a meeting and it will appear here.'
                : 'Try a different search or filter.'}
            </Text>
          </SectionMessage>
        ) : (
          <DynamicTable head={head} rows={rows} rowsPerPage={10} />
        )}
      </Card>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Create a ticket from the meeting notes
// ---------------------------------------------------------------------------

function CreateTicketFromNotes({ meeting }: { meeting: Meeting }) {
  const followUp = `Follow up: ${meeting.title}`;
  const [summary, setSummary] = useState(followUp);
  const [projects, setProjects] = useState<{ label: string; value: string }[] | null>(null);
  const [project, setProject] = useState<{ label: string; value: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; key?: string; error?: string } | null>(null);

  useEffect(() => {
    (invoke('getProjects') as Promise<{ key: string; name: string }[]>).then((list) => {
      const options = list.map((p) => ({ label: `${p.name} (${p.key})`, value: p.key }));
      setProjects(options);
      setProject(options[0] ?? null);
    });
  }, []);

  // Let people start from anything in the notes that Argo didn't turn into a ticket.
  const starters = [
    { label: 'Whole meeting follow-up', value: followUp },
    ...meeting.keyPoints.map((p) => ({ label: `Key point: ${p.slice(0, 70)}`, value: p })),
    ...meeting.decisions.map((d) => ({ label: `Decision: ${d.slice(0, 70)}`, value: d })),
  ];

  const create = async () => {
    setBusy(true);
    const res = (await invoke('createIssue', {
      summary: summary.slice(0, 255),
      projectKey: project?.value,
      description: [
        `From the meeting "${meeting.title}" on ${meeting.date}.`,
        meeting.summary && `Summary: ${meeting.summary}`,
        meeting.keyPoints.length > 0 && `Key points: ${meeting.keyPoints.join('; ')}`,
        meeting.decisions.length > 0 && `Decisions: ${meeting.decisions.join('; ')}`,
      ]
        .filter(Boolean)
        .join('\n\n'),
    })) as { ok: boolean; key?: string; error?: string };
    setResult(res);
    setBusy(false);
  };

  return (
    <Box xcss={highlightStyles}>
      <Stack space="space.200">
        <Stack space="space.050">
          <Heading size="medium">Create ticket from notes</Heading>
          <Text>Argo missed something? Turn anything from these notes into a Jira ticket.</Text>
        </Stack>
        {projects && projects.length === 0 ? (
          <SectionMessage appearance="warning" title="No projects available">
            <Text>Create a Jira software project on this site to turn meeting notes into tickets.</Text>
          </SectionMessage>
        ) : (
          <Stack space="space.150">
            <Select options={projects ?? []} value={project} onChange={(o) => o && setProject(o)} placeholder="Project" />
            <Select
              options={starters}
              placeholder="Start from a key point or decision (optional)"
              onChange={(o) => o && setSummary(o.value)}
            />
            <Textfield value={summary} onChange={(e) => setSummary(e.target.value)} />
            <Inline>
              <Button appearance="primary" isDisabled={busy || !project || !summary.trim()} onClick={create}>
                Create ticket
              </Button>
            </Inline>
          </Stack>
        )}
        {result?.ok && (
          <SectionMessage appearance="success" title={`Created ${result.key}`}>
            <Text>The issue was added to the selected project.</Text>
          </SectionMessage>
        )}
        {result && !result.ok && (
          <SectionMessage appearance="error" title="Could not create ticket">
            <Text>{result.error}</Text>
          </SectionMessage>
        )}
      </Stack>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Meeting detail
// ---------------------------------------------------------------------------

function MeetingDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [transcript, setTranscript] = useState<TranscriptSegment[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () =>
      Promise.all([
        invoke('getMeeting', { id }) as Promise<Meeting>,
        invoke('getTranscript', { id }) as Promise<TranscriptSegment[]>,
      ])
        .then(([m, t]) => {
          if (!active) return;
          setMeeting(m);
          setTranscript(t);
        })
        .catch((e: Error) => active && setError(e.message));
    load();
    const timer = setInterval(load, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [id]);

  const back = (
    <Inline>
      <Button appearance="subtle" onClick={onBack}>← All meetings</Button>
    </Inline>
  );

  if (error)
    return (
      <Stack space="space.200">
        {back}
        <SectionMessage appearance="error" title="Something went wrong"><Text>{error}</Text></SectionMessage>
      </Stack>
    );
  if (!meeting) return <Stack space="space.200">{back}<Spinner size="large" /></Stack>;

  const t0 = meeting.startedAt;

  const commandHead = {
    cells: [cell('Time', 11), cell('What was said', 30), cell('Understood as', 14), cell('Result', 11), cell('Argo replied', 34)],
  };
  const commandRows = meeting.commands.map((c) => ({
    key: c.id,
    cells: [
      { key: 'time', content: <TimeStamp at={c.at} startedAt={t0} suffix=" into meeting" /> },
      { key: 'said', content: <Text weight="medium">{c.transcript}</Text> },
      { key: 'intent', content: c.intent ? <Tag text={c.intent.replace(/_/g, ' ')} color="blue" /> : '—' },
      {
        key: 'result',
        content: <Lozenge appearance={outcomeAppearance[c.outcome]} isBold>{c.outcome.replace('_', ' ')}</Lozenge>,
      },
      { key: 'reply', content: <Text color="color.text.subtle">{c.spokenReply || '—'}</Text> },
    ],
  }));

  const actionHead = {
    cells: [cell('Time', 11), cell('Ticket', 10), cell('What changed', 41), cell('Type', 14), cell('Status', 11)],
  };
  const actionRows = meeting.actions.map((a) => ({
    key: a.id,
    cells: [
      { key: 'time', content: <TimeStamp at={a.at} startedAt={t0} suffix=" into meeting" /> },
      { key: 'ticket', content: <Lozenge appearance="inprogress" isBold>{a.issueKey || '—'}</Lozenge> },
      {
        key: 'what',
        content: (
          <Stack space="space.025">
            <Text weight="medium">{a.title || a.issueKey}</Text>
            <Text size="small" color="color.text.subtle">{a.detail}</Text>
          </Stack>
        ),
      },
      { key: 'type', content: <Tag text={a.kind} color="grey" /> },
      { key: 'status', content: <Lozenge appearance={statusAppearance[a.status]} isBold>{a.status.replace('_', ' ')}</Lozenge> },
    ],
  }));

  const transcriptHead = { cells: [cell('Time', 14), cell('What was said', 86)] };
  const transcriptRows = transcript.map((s, i) => ({
    key: `${i}-${s.at}`,
    cells: [
      { key: 'time', content: <TimeStamp at={s.at} startedAt={t0} /> },
      { key: 'text', content: <Text>{s.text}</Text> },
    ],
  }));

  const executed = meeting.commands.filter((c) => c.outcome === 'executed').length;
  const needHelp = meeting.commands.filter((c) => c.outcome === 'clarify' || c.outcome === 'failed' || c.outcome === 'error').length;

  return (
    <Stack space="space.300">
      {back}

      <Stack space="space.100">
        <Inline space="space.200" alignBlock="center">
          <Heading size="xxlarge">{meeting.title}</Heading>
          <StatusLozenge status={meeting.status} />
        </Inline>
        <Text size="large" color="color.text.subtle">
          {`${formatDate(meeting.startedAt)} · started ${formatClock(meeting.startedAt)}`}
        </Text>
      </Stack>

      <Row>
        <StatCard label="Length" value={formatDuration(meeting.durationSec)} hint={meeting.status === 'live' ? 'Still running' : 'Total time'} />
        <StatCard label="Commands" value={String(meeting.commands.length)} hint={`${executed} executed`} />
        <StatCard label="Jira changes" value={String(meeting.actions.length)} hint={meeting.actions.length === 0 ? 'None made' : 'Tickets touched by Argo'} />
        <StatCard label="Needed follow-up" value={String(needHelp)} hint="Clarify, failed or error" />
      </Row>

      {/* The first tab is selected by default, so the page opens on what Argo did. */}
      <Tabs id="meeting-detail-tabs">
        <TabList>
          <Tab>Commands executed</Tab>
          <Tab>Meeting notes</Tab>
        </TabList>

        <TabPanel>
          <Box paddingBlockStart="space.300">
            <Stack space="space.300">
              <Card title="Commands heard" subtitle={'Every "Hey Argo" request, with how Argo understood it and replied.'}>
                {meeting.commands.length === 0 ? (
                  <Text>{'No "Hey Argo" commands in this meeting.'}</Text>
                ) : (
                  <DynamicTable head={commandHead} rows={commandRows} rowsPerPage={10} />
                )}
              </Card>

              <Card title="Jira changes made" subtitle="The tickets Argo created or changed because of those commands.">
                {meeting.actions.length === 0 ? (
                  <Text>No Jira changes were made.</Text>
                ) : (
                  <DynamicTable head={actionHead} rows={actionRows} rowsPerPage={10} />
                )}
              </Card>
            </Stack>
          </Box>
        </TabPanel>

        <TabPanel>
          <Box paddingBlockStart="space.300">
            <Stack space="space.300">
              <Card title="Summary">
                <Text size="large">
                  {meeting.summary ||
                    (meeting.status === 'live' ? 'The summary is written when the meeting ends.' : 'No summary.')}
                </Text>
              </Card>

              <Row space="space.300">
                <Card title="Key points">
                  {meeting.keyPoints.length === 0 ? (
                    <Text color="color.text.subtle">None recorded.</Text>
                  ) : (
                    <Stack space="space.100">
                      {meeting.keyPoints.map((p) => <Text key={p}>{`• ${p}`}</Text>)}
                    </Stack>
                  )}
                </Card>
                <Card title="Decisions">
                  {meeting.decisions.length === 0 ? (
                    <Text color="color.text.subtle">None recorded.</Text>
                  ) : (
                    <TagGroup>
                      {meeting.decisions.map((d) => <Tag key={d} text={d} color="green" />)}
                    </TagGroup>
                  )}
                </Card>
              </Row>

              {meeting.notes && (
                <Card title="Notes">
                  <Text>{meeting.notes}</Text>
                </Card>
              )}

              <CreateTicketFromNotes meeting={meeting} />

              <Card title="Meeting details">
                <Stack space="space.200">
                  <Row>
                    <Fact label="Date" value={formatDate(meeting.startedAt)} />
                    <Fact label="Started" value={formatClock(meeting.startedAt)} />
                    <Fact label="Ended" value={meeting.endedAt ? formatClock(meeting.endedAt) : 'Still running'} />
                    <Fact label="Length" value={formatDuration(meeting.durationSec)} />
                  </Row>
                  <Row>
                    <Fact label="Speech turns" value={String(transcript.length)} />
                    <Fact label="Commands" value={String(meeting.commands.length)} />
                    <Fact label="Jira changes" value={String(meeting.actions.length)} />
                    <Fact label="Meeting ID" value={meeting.id} />
                  </Row>
                </Stack>
              </Card>

              <Card title="Transcript" subtitle="Everything the listener heard, with timestamps. Speech recognition can make mistakes.">
                {transcript.length === 0 ? (
                  <Text color="color.text.subtle">No speech has been captured yet.</Text>
                ) : (
                  <DynamicTable head={transcriptHead} rows={transcriptRows} rowsPerPage={8} />
                )}
              </Card>
            </Stack>
          </Box>
        </TabPanel>
      </Tabs>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function App() {
  const [meetings, setMeetings] = useState<Meeting[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    void view.theme.enable();
    const load = () =>
      (invoke('getMeetings') as Promise<Meeting[]>)
        .then((list) => {
          setMeetings(list);
          setError(null);
        })
        .catch((e: Error) => setError(e.message));
    load();
    // Poll so a meeting that is still live keeps updating.
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  if (error && !meetings)
    return (
      <SectionMessage appearance="error" title="Could not load meetings">
        <Text>{error}</Text>
      </SectionMessage>
    );
  if (!meetings) return <Spinner size="large" />;
  return openId ? (
    <MeetingDetail id={openId} onBack={() => setOpenId(null)} />
  ) : (
    <MeetingList meetings={meetings} onOpen={setOpenId} />
  );
}
