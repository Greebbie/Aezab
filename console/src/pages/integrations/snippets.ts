export function formatJson(value: unknown) {
  return JSON.stringify(value, null, 2);
}

export function buildInvokePayload(agentId: string, message = 'I need to create a repair ticket') {
  return {
    agent_id: agentId || 'your-agent-id',
    session_id: 'customer-session-123',
    user_id: 'customer-001',
    message,
    client_meta: {
      channel: 'web',
      crm_customer_id: 'C1001',
    },
  };
}

export function buildCurlInvoke(apiBase: string, agentId: string) {
  return `curl -X POST "${apiBase}/invoke" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: <your-api-key>" \\
  -d '${formatJson(buildInvokePayload(agentId))}'`;
}

export function buildJsInvoke(apiBase: string, agentId: string) {
  return `const response = await fetch("${apiBase}/invoke", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": "<your-api-key>"
  },
  body: JSON.stringify(${formatJson(buildInvokePayload(agentId))})
});

const result = await response.json();
console.log(result.short_answer, result.trace_id);`;
}

export function buildSseSnippet(apiBase: string, agentId: string) {
  return `const response = await fetch("${apiBase}/invoke/stream", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": "<your-api-key>"
  },
  body: JSON.stringify(${formatJson(buildInvokePayload(agentId))})
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  console.log(decoder.decode(value, { stream: true }));
}`;
}

export function buildAsrSnippet(apiBase: string) {
  return `curl -X POST "${apiBase}/asr/transcribe" \\
  -H "X-API-Key: <your-api-key>" \\
  -F "file=@voice.wav"`;
}

export function buildToolRegistrationSnippet(apiBase: string) {
  return `curl -X POST "${apiBase}/tools/" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: <your-api-key>" \\
  -d '${formatJson({
    name: 'create_work_order',
    description: 'Create a work order in the customer CRM',
    category: 'api',
    endpoint: 'https://customer-crm.example.com/api/work-orders',
    method: 'POST',
    input_schema: {
      type: 'object',
      properties: {
        customer_id: { type: 'string' },
        issue: { type: 'string' },
      },
      required: ['customer_id', 'issue'],
    },
    auth_config: {
      type: 'bearer',
      token: '<customer-system-token>',
    },
  })}'`;
}

export const responseContract = {
  short_answer: 'Message to show to the end user',
  expanded_answer: 'Optional long answer',
  citations: ['Knowledge references when RAG was used'],
  workflow_card: 'Fields to render when the agent is collecting workflow input',
  workflow_status: 'waiting_input | in_progress | completed | escalated',
  trace_id: 'Use this id in Audit when debugging',
};
