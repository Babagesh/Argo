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
import type { Meeting } from '../mockData';

const NOW = new Date('2026-10-24').getTime();
const statusAppearance = { done: 'success', pending: 'inprogress', failed: 'removed' } as const;

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
        .filter((m) => filter !== 'week' || NOW - new Date(m.date).getTime() <= 7 * 864e5)
        .filter((m) => filter !== 'actions' || m.actions.length >= 3)
        .sort((a, b) =>
          sort.value === 'actions' ? b.actions.length - a.actions.length : b.date.localeCompare(a.date),
        ),
    [meetings, query, filter, sort],
  );

  const head = {
    cells: ['Meeting', 'Date', 'Duration', 'Attendees', 'Agent actions'].map((content) => ({ key: content, content })),
  };
  const rows = visible.map((m) => ({
    key: m.id,
    cells: [
      { key: 'title', content: <Button appearance="subtle" onClick={() => onOpen(m.id)}>{m.title}</Button> },
      { key: 'date', content: m.date },
      { key: 'duration', content: `${m.durationMin} min` },
      { key: 'attendees', content: String(m.attendees.length) },
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
        <SectionMessage title="No meetings match" appearance="information">
          <Text>Try a different search or filter.</Text>
        </SectionMessage>
      ) : (
        <DynamicTable head={head} rows={rows} />
      )}
    </Stack>
  );
}

function CreateTicket({ meeting }: { meeting: Meeting }) {
  const [summary, setSummary] = useState(`Follow up: ${meeting.title}`);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; key?: string; error?: string } | null>(null);

  const create = async () => {
    setBusy(true);
    const res = (await invoke('createIssue', {
      summary,
      description: `${meeting.summary}\n\nDecisions: ${meeting.decisions.join('; ')}`,
    })) as { ok: boolean; key?: string; error?: string };
    setResult(res);
    setBusy(false);
  };

  return (
    <Stack space="space.150">
      <Heading size="small">Create a Jira ticket from this meeting</Heading>
      <Textfield value={summary} onChange={(e) => setSummary(e.target.value)} />
      <Inline>
        <Button appearance="primary" isDisabled={busy} onClick={create}>
          Create ticket
        </Button>
      </Inline>
      {result?.ok && (
        <SectionMessage appearance="success" title={`Created ${result.key}`}>
          <Text>The issue was added to this project's backlog.</Text>
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
      { key: 'detail', content: `${a.kind} · ${a.detail} · ${a.at}` },
    ],
  }));

  return (
    <Stack space="space.250">
      {back}
      <Stack space="space.050">
        <Heading size="large">{meeting.title}</Heading>
        <Text>{`${meeting.date} · ${meeting.durationMin} min · ${meeting.attendees.join(', ')}`}</Text>
      </Stack>

      <Box padding="space.200" backgroundColor="color.background.neutral">
        <Stack space="space.150">
          <Heading size="medium">Summary</Heading>
          <Text>{meeting.summary}</Text>
          <Text><Strong>Key points</Strong></Text>
          <Stack space="space.050">
            {meeting.keyPoints.map((p) => <Text key={p}>{`• ${p}`}</Text>)}
          </Stack>
          <Text><Strong>Decisions</Strong></Text>
          <Inline space="space.100" shouldWrap>
            {meeting.decisions.map((d) => <Lozenge key={d}>{d}</Lozenge>)}
          </Inline>
        </Stack>
      </Box>

      <Stack space="space.100">
        <Heading size="medium">Meeting notes</Heading>
        <Text>{meeting.notes}</Text>
      </Stack>

      <Stack space="space.100">
        <Heading size="medium">Agent actions</Heading>
        <DynamicTable head={head} rows={rows} />
      </Stack>

      <CreateTicket meeting={meeting} />
    </Stack>
  );
}

export default function App() {
  const [meetings, setMeetings] = useState<Meeting[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    void view.theme.enable();
    (invoke('getMeetings') as Promise<Meeting[]>).then(setMeetings);
  }, []);

  if (!meetings) return <Spinner size="large" />;
  return openId ? (
    <MeetingDetail id={openId} onBack={() => setOpenId(null)} />
  ) : (
    <MeetingList meetings={meetings} onOpen={setOpenId} />
  );
}
