import React, { useEffect, useState } from 'react';
import ForgeReconciler, { Lozenge, SectionMessage, Spinner, Stack, Text, useProductContext } from '@forge/react';
import { invoke, view } from '@forge/bridge';
import type { AgentAction } from '../types';

function Panel() {
  const context = useProductContext();
  const [actions, setActions] = useState<AgentAction[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const issueKey = (context?.extension as { issue?: { key?: string } } | undefined)?.issue?.key;

  useEffect(() => {
    void view.theme.enable();
  }, []);

  useEffect(() => {
    if (!issueKey) return;
    (invoke('getIssueActions', { key: issueKey }) as Promise<AgentAction[]>)
      .then(setActions)
      .catch((e: Error) => setError(e.message));
  }, [issueKey]);

  if (error)
    return (
      <SectionMessage appearance="error" title="Could not load Argo activity">
        <Text>{error}</Text>
      </SectionMessage>
    );
  if (!actions || !issueKey) return <Spinner />;

  if (actions.length === 0)
    return (
      <SectionMessage appearance="information" title="No meeting activity">
        <Text>{`Argo hasn't acted on ${issueKey} from any meeting yet.`}</Text>
      </SectionMessage>
    );

  return (
    <Stack space="space.100">
      {actions.map((a) => (
        <Stack key={a.id} space="space.050">
          <Text>{`${a.kind}: ${a.title || a.issueKey}`}</Text>
          <Lozenge appearance={a.status === 'failed' ? 'removed' : a.status === 'done' ? 'success' : 'default'}>
            {a.status.replace('_', ' ')}
          </Lozenge>
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
