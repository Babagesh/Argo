import Resolver from '@forge/resolver';
import api, { route } from '@forge/api';
import { MOCK_MEETINGS } from './mockData';

const resolver = new Resolver();

resolver.define('ping', () => ({ ok: true }));

// Swap these for Forge storage (@forge/kvs) or the agent server when the backend exists.
resolver.define('getMeetings', () => MOCK_MEETINGS);

resolver.define('getMeeting', ({ payload }) => {
  const meeting = MOCK_MEETINGS.find((m) => m.id === payload.id);
  if (!meeting) throw new Error('Meeting not found');
  return meeting;
});

// Lists projects the current user can see, for choosing where to create tickets.
resolver.define('getProjects', async () => {
  const res = await api.asUser().requestJira(route`/rest/api/3/project/search?maxResults=50`, {
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) return [];
  const data = (await res.json()) as { values: { key: string; name: string }[] };
  return data.values.map((p) => ({ key: p.key, name: p.name }));
});

// Creates a real Jira issue as the current user in the chosen project.
resolver.define('createIssue', async ({ payload, context }) => {
  const projectKey: string | undefined = payload.projectKey ?? context.extension?.project?.key;
  if (!projectKey) return { ok: false, error: 'Choose a project first.' };
  const summary = String(payload.summary ?? '').trim();
  if (!summary) return { ok: false, error: 'Summary is required.' };

  const description = {
    type: 'doc',
    version: 1,
    content: [
      {
        type: 'paragraph',
        content: [{ type: 'text', text: String(payload.description ?? 'Created by Argo from a meeting summary.') }],
      },
    ],
  };

  const res = await api.asUser().requestJira(route`/rest/api/3/issue`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({
      fields: { project: { key: projectKey }, summary, issuetype: { name: 'Task' }, description },
    }),
  });

  if (!res.ok) return { ok: false, error: `Jira returned ${res.status}: ${await res.text()}` };
  const issue = (await res.json()) as { key: string };
  return { ok: true, key: issue.key };
});

export const uiHandler = resolver.getDefinitions();

export const webhookHandler = async (request: unknown) => {
  console.log('agent-action-webhook received', JSON.stringify(request));
  return { statusCode: 200, body: 'ok' };
};
