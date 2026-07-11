# hlab-client

Official Python SDK for the [HlAB](https://github.com/AbysenAI/aezab) Headless AI Agent Platform. Zero
dependencies beyond [`httpx`](https://www.python-httpx.org/) (`>=0.24`). Provides both a synchronous
(`HlabClient`) and an asyncio-based (`AsyncHlabClient`) client with an identical method surface.

Full API reference and SSE event contract: see
[`docs/integration.md`](https://github.com/AbysenAI/aezab/blob/main/docs/integration.md) in the main repo.

## Install

This SDK is not yet published to PyPI. Install it from source:

```bash
pip install -e sdk/python   # from a clone of the main repo
```

Once published, it will be installable with:

```bash
pip install hlab-client   # not yet available
```

## Quickstart

### Synchronous call

```python
from hlab_client import HlabClient, HlabAPIError

with HlabClient("https://your-hlab-host", api_key="...") as client:
    try:
        result = client.invoke("sales_agent", "Hello, what can you do?")
    except HlabAPIError as exc:
        # exc.status_code, exc.detail — mirrors the API's {"detail": ...} error body
        raise
    print(result["short_answer"])
```

### Streaming call (SSE)

```python
from hlab_client import HlabClient

with HlabClient("https://your-hlab-host", api_key="...") as client:
    for event in client.invoke_stream("sales_agent", "Hello"):
        if event["event"] == "answer_delta":
            print(event["data"]["text"], end="", flush=True)
        elif event["event"] == "status":
            print(f"\n[status] {event['data']}")
        elif event["event"] == "done":
            print(f"\n[done] session_id={event['data']['session_id']}")
        elif event["event"] == "error":
            print(f"\n[error] {event['data']['error_msg']}")
```

### Async client

```python
from hlab_client import AsyncHlabClient

async def main():
    async with AsyncHlabClient("https://your-hlab-host", api_key="...") as client:
        async for event in client.invoke_stream("sales_agent", "Hello"):
            ...
```

### Sessions / Files

```python
sessions = client.list_sessions(agent_id="sales_agent", limit=20)
messages = client.get_messages(sessions["items"][0]["id"])
client.delete_session(sessions["items"][0]["id"])

uploaded = client.upload_file("/path/to/quote.pdf")
# uploaded["reference"] == "file://<file_id>" — put this string into a
# workflow's form_data for a field_type="file" collect step.
result = client.invoke("sales_agent", "here is my file", form_data={"attachment": uploaded["reference"]})
```

## Authentication

All requests are authenticated with an `X-API-Key` header. Create a scoped key via the HlAB console or
`POST /api/v1/auth/api-keys` — use an `invoke`-scoped key for this SDK, never a `manage`-scoped key, in
any client-side or externally-shared code.

## License

MIT
