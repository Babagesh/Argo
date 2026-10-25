import React, { useEffect, useMemo, useState } from 'react';
import {
  Badge,
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
  Text,
  Textfield,
} from '@forge/react';
import { invoke, view } from '@forge/bridge';
import type { Command, Meeting } from '../types';

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
const formatTime = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

const SORT_OPTIONS = [
  { label: 'Newest first', value: 'newest' },
  { label: 'Most actions', value: 'actions' },
];

function MeetingList({ meetings, onOpen }: { meetings: Meeting[]; onOpen: (id: string) => void }) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<'all' | 'week' | 'actions'>('all');
  const [sort, setSort] = useState(SORT_OPTIONS[0]);

  const visible = useMemo(
    () =>
      meetings
        .filter((m) => m.title.toLowerCase().includes(query.toLowerCase()))
        .filter((m) => filter !== 'week' || Date.now() - new Date(m.startedAt).getTime() <= 7 * 864e5)
        .filter((m) => filter !== 'actions' || m.actions.length >= 3)
        .sort((a, b) =>
          sort.value === 'actions' ? b.actions.length - a.actions.length : b.startedAt.localeCompare(a.startedAt),
        ),
    [meetings, query, filter, sort],
  );

  const head = {
    cells: ['Meeting', 'Date', 'Length', 'Status', 'Commands', 'Agent actions'].map((content) => ({ key: content, content })),
  };
  const rows = visible.map((m) => ({
    key: m.id,
    cells: [
      { key: 'title', content: <Button appearance="subtle" onClick={() => onOpen(m.id)}>{m.title}</Button> },
      { key: 'date', content: m.date },
      { key: 'duration', content: formatDuration(m.durationSec) },
      {
        key: 'status',
        content: <Lozenge appearance={m.status === 'live' ? 'inprogress' : 'success'}>{m.status === 'live' ? 'Live' : 'Complete'}</Lozenge>,
      },
      { key: 'commands', content: String(m.commands.length) },
      { key: 'actions', content: <Badge appearance="primary">{String(m.actions.length)}</Badge> },
    ],
  }));

  return (
    <Stack space="space.200">
      <Stack space="space.050">
        <Heading size="large">Meeting reports</Heading>
        <Text>Summaries and the Jira actions your agent took.</Text>
      </Stack>
      <Inline space="space.200" alignBlock="end">
        <Textfield placeholder="Search meetings" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Select options={SORT_OPTIONS} value={sort} onChange={(o) => o && setSort(o)} />
      </Inline>
      <Tabs id="meeting-filter" onChange={(i) => setFilter((['all', 'week', 'actions'] as const)[i])}>
        <TabList>
          <Tab>All</Tab>
          <Tab>This week</Tab>
          <Tab>Many actions</Tab>
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
        <DynamicTable head={head} rows={rows} />
      )}
    </Stack>
  );
}

function CreateTicket({ meeting }: { meeting: Meeting }) {
  const [summary, setSummary] = useState(`Follow up: ${meeting.title}`);
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

  const create = async () => {
    setBusy(true);
    const res = (await invoke('createIssue', {
      summary,
      projectKey: project?.value,
      description: `${meeting.summary}\n\nDecisions: ${meeting.decisions.join('; ')}`,
    })) as { ok: boolean; key?: string; error?: string };
    setResult(res);
    setBusy(false);
  };

  return (
    <Stack space="space.150">
      <Heading size="small">Create a Jira ticket from this meeting</Heading>
      {projects && projects.length === 0 ? (
        <SectionMessage appearance="warning" title="No projects available">
          <Text>Create a Jira software project on this site to turn meeting actions into tickets.</Text>
        </SectionMessage>
      ) : (
        <>
          <Select options={projects ?? []} value={project} onChange={(o) => o && setProject(o)} placeholder="Project" />
          <Textfield value={summary} onChange={(e) => setSummary(e.target.value)} />
          <Inline>
            <Button appearance="primary" isDisabled={busy || !project} onClick={create}>
              Create ticket
            </Button>
          </Inline>
        </>
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
  );
}

function MeetingDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () =>
      (invoke('getMeeting', { id }) as Promise<Meeting>)
        .then((m) => active && setMeeting(m))
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

  const head = { cells: ['Issue', 'Title', 'Status', 'Detail'].map((content) => ({ key: content, content })) };
  const rows = meeting.actions.map((a) => ({
    key: a.id,
    cells: [
      { key: 'issue', content: <Lozenge appearance="inprogress">{a.issueKey}</Lozenge> },
      { key: 'title', content: a.title },
      { key: 'status', content: <Lozenge appearance={statusAppearance[a.status]}>{a.status}</Lozenge> },
      { key: 'detail', content: `${a.kind} · ${a.detail} · ${formatTime(a.at)}` },
    ],
  }));

  const commandHead = { cells: ['Time', 'Heard', 'Result'].map((content) => ({ key: content, content })) };
  const commandRows = meeting.commands.map((c) => ({
    key: c.id,
    cells: [
      { key: 'time', content: formatTime(c.at) },
      { key: 'heard', content: c.transcript },
      {
        key: 'result',
        content: (
          <Inline space="space.100" alignBlock="center">
            <Lozenge appearance={outcomeAppearance[c.outcome]}>{c.outcome.replace('_', ' ')}</Lozenge>
            <Text>{c.spokenReply}</Text>
          </Inline>
        ),
      },
    ],
  }));

  return (
    <Stack space="space.250">
      {back}
      <Stack space="space.050">
        <Inline space="space.150" alignBlock="center">
          <Heading size="large">{meeting.title}</Heading>
          <Lozenge appearance={meeting.status === 'live' ? 'inprogress' : 'success'}>
            {meeting.status === 'live' ? 'Live' : 'Complete'}
          </Lozenge>
        </Inline>
        <Text>{`${meeting.date} · started ${formatTime(meeting.startedAt)} · ${formatDuration(meeting.durationSec)}`}</Text>
      </Stack>

      <Box padding="space.200" backgroundColor="color.background.neutral">
        <Stack space="space.150">
          <Heading size="medium">Summary</Heading>
          <Text>
            {meeting.summary || (meeting.status === 'live' ? 'The summary is written when the meeting ends.' : 'No summary.')}
          </Text>
          {meeting.keyPoints.length > 0 && (
            <>
              <Text><Strong>Key points</Strong></Text>
              <Stack space="space.050">
                {meeting.keyPoints.map((p) => <Text key={p}>{`• ${p}`}</Text>)}
              </Stack>
            </>
          )}
          {meeting.decisions.length > 0 && (
            <>
              <Text><Strong>Decisions</Strong></Text>
              <Inline space="space.100" shouldWrap>
                {meeting.decisions.map((d) => <Lozenge key={d}>{d}</Lozenge>)}
              </Inline>
            </>
          )}
        </Stack>
      </Box>

      {meeting.notes && (
        <Stack space="space.100">
          <Heading size="medium">Meeting notes</Heading>
          <Text>{meeting.notes}</Text>
        </Stack>
      )}

      <Stack space="space.100">
        <Heading size="medium">Commands heard</Heading>
        {meeting.commands.length === 0 ? (
          <Text>{'No "Hey Argo" commands in this meeting.'}</Text>
        ) : (
          <DynamicTable head={commandHead} rows={commandRows} />
        )}
      </Stack>

      <Stack space="space.100">
        <Heading size="medium">Agent actions</Heading>
        {meeting.actions.length === 0 ? <Text>No Jira changes were made.</Text> : <DynamicTable head={head} rows={rows} />}
      </Stack>

      <CreateTicket meeting={meeting} />
    </Stack>
  );
}

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
