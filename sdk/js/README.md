# hlab-client

Official JavaScript/TypeScript SDK for the [HlAB](https://github.com/AbysenAI/aezab) Headless AI Agent
Platform. Zero runtime dependencies — built on the browser/Node 18+ built-in `fetch` and
`ReadableStream`.

Full API reference and SSE event contract: see
[`docs/integration.md`](https://github.com/AbysenAI/aezab/blob/main/docs/integration.md) in the main repo.

## Install

This SDK is not yet published to npm. Install it from source:

```bash
npm install ./sdk/js                        # from a clone of the main repo, or
cd sdk/js && npm install && npm run build    # build locally and import from dist/
```

Once published, it will be installable with:

```bash
npm install hlab-client   # not yet available
```

## Quickstart

```ts
import { HlabClient, HlabAPIError } from "hlab-client";

const client = new HlabClient("https://your-hlab-host", { apiKey: "..." });

// Synchronous-style call (still returns a Promise, like any fetch-based API)
try {
  const result = await client.invoke("sales_agent", "Hello, what can you do?");
  console.log(result.short_answer);
} catch (err) {
  if (err instanceof HlabAPIError) {
    console.error(err.statusCode, err.detail);
  }
}

// Streaming call
for await (const event of client.invokeStream("sales_agent", "Hello")) {
  if (event.event === "answer_delta") {
    process.stdout.write((event.data as { text: string }).text);
  } else if (event.event === "done") {
    console.log("\ndone", event.data);
  }
}

// Sessions / files
const sessions = await client.listSessions({ agentId: "sales_agent", limit: 20 });
const messages = await client.getMessages(sessions.items[0].id);
await client.deleteSession(sessions.items[0].id);

const uploaded = await client.uploadFile(fileBlob, "quote.pdf");
```

## Authentication

All requests are authenticated with an `X-API-Key` header (pass it via the `apiKey` client option).
Create a scoped key via the HlAB console or `POST /api/v1/auth/api-keys` — use an `invoke`-scoped key
for this SDK, never a `manage`-scoped key, in any browser-exposed code.

## Build

```bash
npm install
npm run build   # compiles src/index.ts -> dist/ via tsc
```

## License

MIT
