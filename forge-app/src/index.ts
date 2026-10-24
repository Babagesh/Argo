import Resolver from '@forge/resolver';

const resolver = new Resolver();

resolver.define('ping', () => ({ ok: true }));

export const uiHandler = resolver.getDefinitions();

export const webhookHandler = async (request: unknown) => {
  console.log('agent-action-webhook received', JSON.stringify(request));
  return { statusCode: 200, body: 'ok' };
};
