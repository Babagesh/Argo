import React, { useEffect, useState } from 'react';
import ForgeReconciler, { Lozenge, SectionMessage, Spinner, Stack, Text, useProductContext } from '@forge/react';
import { invoke, view } from '@forge/bridge';
import type { Meeting } from '../mockData';

function Panel() {
  const context = useProductContext();
  const [meetings, setMeetings] = useState<Meeting[] | null>(null);

  useEffect(() => {
    void view.theme.enable();
    (invoke('getMeetings') as Promise<Meeting[]>).then(setMeetings);
  }, []);

  const issueKey = (context?.extension as { issue?: { key?: string } } | undefined)?.issue?.key;
  if (!meetings || !issueKey) return <Spinner />;

  const actions = meetings.flatMap((m) =>
    m.actions.filter((a) => a.issueKey === issueKey).map((a) => ({ ...a, meeting: m.title })),
  );

  if (actions.length === 0)
    return (
      <SectionMessage appearance="information" title="No meeting activity">
        <Text>{`Argo's agent hasn't acted on ${issueKey} from any meeting yet.`}</Text>
      </SectionMessage>
    );

  return (
    <Stack space="space.100">
      {actions.map((a) => (
        <Stack key={a.id} space="space.050">
          <Text>{`${a.kind}: ${a.title}`}</Text>
          <Lozenge>{a.meeting}</Lozenge>
        </Stack>
      ))}
    </Stack>
  );
}

ForgeReconciler.render(
  <React.StrictMode>
    <Panel />
  </React.StrictMode>,
);
