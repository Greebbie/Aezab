# HlAB 集成指南

> 面向外部系统 / 客户端应用的 Headless API 集成文档。覆盖：API Key 获取、Python/JS
> 官方 SDK（同步调用 + SSE 流式）、SSE 事件参考、会话（Sessions）与文件（Files）
> API、事件订阅（Webhook）与签名验证、限流 / 重试 / 幂等性指引、嵌入式聊天 Widget。
> 生产部署清单见 [`docs/deployment.md`](./deployment.md)。
>
> 代码示例注释为英文，正文说明为中文。

---

## 1. 获取 API Key

所有 Headless API 调用都通过 `X-API-Key` 请求头认证。API Key 携带 **scopes**（作用域），
调用 `/invoke` 系列接口需要 `invoke` 作用域；调用管理类接口（agents/workflows/knowledge/
tools/subscriptions 等）需要 `manage` 作用域。空 scopes 列表表示不限制（向后兼容旧 Key）。

```bash
# 1) 登录获取 JWT（仅用于创建 API Key 这一步，业务调用不需要 JWT）
curl -X POST "$HLAB_HOST/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "***"}'
# -> {"access_token": "...", ...}

# 2) 用 JWT 创建一个只带 invoke 作用域的 API Key（原始 key 仅在此响应中出现一次，请妥善保存）
curl -X POST "$HLAB_HOST/api/v1/auth/api-keys" \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-integration", "scopes": ["invoke"]}'
# -> {"id": "...", "name": "my-integration", "key": "<raw-key-save-this>", "tenant_id": "...", "scopes": ["invoke"]}
```

之后所有请求带上：

```
X-API-Key: <raw-key>
```

---

## 2. Python SDK

代码位置：`sdk/python/hlab_client/`（包名 `hlab-client`，仅依赖 `httpx>=0.24`）。

**SDK 尚未发布到 PyPI**，目前只能从源码安装：

```bash
pip install -e sdk/python   # 从源码安装（当前唯一可用方式）
# pip install hlab-client   # 发布到 PyPI 后可用，尚未发布
```

### 2.1 同步调用

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

### 2.2 流式调用（SSE）

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

### 2.3 异步客户端

`AsyncHlabClient` 提供与 `HlabClient` 完全一致的方法（`invoke` / `invoke_stream` /
`list_sessions` / `get_messages` / `delete_session` / `upload_file`），基于
`httpx.AsyncClient`，用 `async for` 消费 `invoke_stream`：

```python
from hlab_client import AsyncHlabClient

async def main():
    async with AsyncHlabClient("https://your-hlab-host", api_key="...") as client:
        async for event in client.invoke_stream("sales_agent", "Hello"):
            ...
```

### 2.4 Sessions / Files

```python
sessions = client.list_sessions(agent_id="sales_agent", limit=20)
messages = client.get_messages(sessions["items"][0]["id"])
client.delete_session(sessions["items"][0]["id"])

uploaded = client.upload_file("/path/to/quote.pdf")
# uploaded["reference"] == "file://<file_id>" — put this string into a
# workflow's form_data for a field_type="file" collect step.
result = client.invoke("sales_agent", "here is my file", form_data={"attachment": uploaded["reference"]})
```

---

## 3. JavaScript / TypeScript SDK

代码位置：`sdk/js/`（包名 `hlab-client`，零运行时依赖，基于浏览器 / Node 18+ 内置的
`fetch` + `ReadableStream`）。与 Python SDK 不同，JS 的 `fetch` 天然是异步的，因此只有
一个 `HlabClient` 类，没有单独的“同步/异步”两套客户端。

**SDK 尚未发布到 npm**，目前只能从源码安装/引用：

```bash
npm install ./sdk/js       # 从源码安装（当前唯一可用方式），或
cd sdk/js && npm install && npm run build   # 本地构建后直接引用 dist/
# npm install hlab-client  # 发布到 npm 后可用，尚未发布
```

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

编译检查：`npx tsc --noEmit -p sdk/js/tsconfig.json`。

---

## 4. SSE 事件参考（`POST /invoke/stream`）

流式端点通过 `text/event-stream` 推送以下事件；SDK 的 `invoke_stream` /
`invokeStream` 会把每一帧解析为 `{event, data}`：

| event 名 | 触发时机 | data 结构（示例） |
|---|---|---|
| `status` (`stage: "retrieval"`) | 知识预检索开始/结束 | 开始：`{"stage": "retrieval", "domains": ["default"]}`；结束：`{"stage": "retrieval", "hits": 3}` |
| `status` (`stage: "tool"`) | 每次函数调用（工具/技能）开始/结束 | 开始：`{"stage": "tool", "name": "knowledge_search", "round": 1, "state": "started"}`；结束：`{"stage": "tool", "name": "knowledge_search", "round": 1, "state": "finished", "ok": true}` |
| `answer_delta` | LLM 流式生成过程中的增量文本 | `{"text": "您好"}` — 逐块追加，客户端应拼接展示 |
| `answer` | 最终答案（完整文本，一次性） | `{"content": "您好，我可以帮您..."}` — **权威最终结果**：即使消费了 `answer_delta`，最终展示也应以这条为准（可能因为拒答兜底/后处理与增量拼接结果不同） |
| `done` | 管道结束，携带元数据 | `{"session_id": "...", "trace_id": "...", "citations": [...], "followups": [...], "expanded_answer": "...", "workflow_card": {...}\|null, "workflow_status": "in_progress"\|"waiting_input"\|"completed"\|"escalated"\|null, "escalated": false, "escalation_reason": null, "skill_info": {...}\|null, "metadata": {...}\|null}` |
| `error` | 管道中任意阶段失败 | `{"detail": "...", "error_type": "timeout"\|"rate_limit"\|"llm_error"\|"retrieval"\|"workflow"\|"tool"\|"platform"\|"not_found"\|"request"\|"internal", "error_msg": "面向用户的中文错误提示"}` |

不消费 `answer_delta` / `status` 的旧客户端不受影响：只处理 `answer` 与 `done`
两个事件即可拿到与同步 `/invoke` 完全一致的结果。

同步端点 `POST /invoke` 直接返回上述 `done` 事件里的字段结构（`InvokeResponse`
JSON），不涉及事件流。

---

## 5. Sessions API

`GET /api/v1/sessions/` — 按租户列出会话，支持 `agent_id` / `user_id` 过滤和
`limit`（默认 50，≤200）/ `offset` 分页：

```json
{"total": 12, "offset": 0, "limit": 50, "items": [
  {"id": "...", "agent_id": "...", "user_id": "...", "status": "active",
   "message_count": 4, "created_at": "...", "updated_at": "...", "title": "..."}
]}
```

`GET /api/v1/sessions/{session_id}/messages` — 按时间正序返回消息，分页结构同上
（`limit` 默认 100，≤500）。跨租户访问一律 404。

`DELETE /api/v1/sessions/{session_id}` — 级联删除会话及其消息，返回 204。

---

## 6. Files API

`POST /api/v1/files/upload`（`multipart/form-data`，字段名 `file`）— 上传文件供
workflow 的 `field_type="file"` 收集步骤使用：

```json
{"file_id": "...", "filename": "quote.pdf", "size": 12345,
 "content_type": "application/pdf", "reference": "file://<file_id>"}
```

把 `reference` 原样放进后续 `/invoke` 请求的 `form_data` 里对应字段即可。允许的扩展名：
`.jpg` `.jpeg` `.png` `.webp` `.pdf`；大小上限由服务端 `MAX_UPLOAD_MB`（默认 10MB）配置。

`GET /api/v1/files/{file_id}` — 按租户下载文件（跨租户 404）。

---

## 7. 事件订阅（Webhook）与签名验证

除了主动轮询 Sessions API，还可以注册 **事件订阅**，让平台在特定事件发生时主动回调
你的服务。

### 7.1 管理订阅（需要 `manage` 作用域的 API Key）

```
GET    /api/v1/subscriptions/            列出当前租户的订阅
POST   /api/v1/subscriptions/            创建订阅
GET    /api/v1/subscriptions/{id}        查看单个订阅
PUT    /api/v1/subscriptions/{id}        更新订阅
DELETE /api/v1/subscriptions/{id}        删除订阅
```

创建请求体：

```json
{
  "name": "workflow-notifications",
  "url": "https://your-service.example.com/hooks/hlab",
  "secret": "<a-random-shared-secret-you-generate>",
  "events": ["workflow.completed", "workflow.escalated"],
  "enabled": true
}
```

`events` 也可以填 `["*"]` 订阅全部事件类型。

**内网地址默认被拦截（SSRF 防护）**：创建订阅时如果 `url` 指向内网/回环地址
（`localhost`、`127.0.0.1`、`10.x`、`192.168.x` 等私有网段，详见
`server/engine/event_dispatcher.py` 的地址校验逻辑），请求会被直接拒绝，投递时也会
被跳过——这是为了防止外部租户拿 webhook 当跳板探测你的内网。如果你确实需要在本机或
内网环境联调（例如 webhook 接收端和 Aezab 部署在同一台机器/同一个内网），显式设置
`AEZAB_ALLOW_INTERNAL_WEBHOOKS=true` 放行；生产环境不建议长期打开这个开关。

目前会发出的事件：

| event_type | 触发时机 | payload 关键字段 |
|---|---|---|
| `workflow.completed` | 某个 complete 步骤成功完成（含 webhook 成功或无 webhook） | `session_id, workflow_id, agent_id, idempotency_key, data`（用户填写的非下划线字段） |
| `workflow.submit_failed` | complete 步骤的 webhook 重试耗尽仍失败 | 同上 + `error` |
| `workflow.escalated` | 人工审核暂停，或工具调用 `on_failure="escalate"` | `session_id, workflow_id, step_name, reason` |

### 7.2 投递格式

平台以 `POST` 方式向 `url` 发送 JSON：

```json
{"event_type": "workflow.completed", "timestamp": "2026-07-11T08:00:00+00:00", "payload": {...}}
```

请求头：

```
X-HlAB-Event: workflow.completed
X-HlAB-Signature: sha256=<hex-hmac-sha256-of-raw-body-using-your-secret>
```

超时 10 秒，失败按 1s / 2s / 4s 退避重试 3 次；全部失败也不会影响平台本身的业务流程
（fire-and-forget，仅记录日志）。

### 7.3 验签示例（Python / FastAPI 接收端）

```python
import hashlib
import hmac

from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
WEBHOOK_SECRET = "<the-secret-you-set-when-creating-the-subscription>"


def verify_hlab_signature(secret: str, raw_body: bytes, signature_header: str | None) -> bool:
    """Recompute HMAC-SHA256 over the raw request body and compare in constant time."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header[len("sha256="):]
    return hmac.compare_digest(expected, provided)


@app.post("/hooks/hlab")
async def receive_hlab_event(request: Request):
    raw_body = await request.body()  # must verify against the RAW bytes, not parsed JSON
    signature = request.headers.get("X-HlAB-Signature")

    if not verify_hlab_signature(WEBHOOK_SECRET, raw_body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event_type = request.headers.get("X-HlAB-Event")
    body = await request.json()
    # ... handle body["event_type"], body["timestamp"], body["payload"]
    return {"ok": True}
```

**要点**：一定要对**原始请求体字节**计算 HMAC，不要先 `json.loads` 再 `json.dumps`
重新序列化后再验签——键顺序/空白差异会导致签名不匹配。

---

## 8. 限流 / 重试 / 幂等性

### 8.1 限流

`/invoke` 与 `/invoke/stream` 受滑动窗口限流保护，默认每个 API Key 每分钟
`RATE_LIMIT_PER_MINUTE`（默认 60）次请求（无 API Key 时退化为按用户 ID，再退化为
按客户端 IP）。超限返回 `429`，响应头带 `Retry-After: <seconds>`。SDK 不会自动重试
429——请在客户端按 `Retry-After` 提示的秒数等待后重试。

### 8.2 客户端重试语义

- **网络级失败 / 5xx / 超时**：可以安全重试，因为 `/invoke` 是幂等无副作用地重新
  处理同一条消息（不会自动去重，会作为新的一轮对话被处理）。若你需要精确的“同一条
  消息只处理一次”的语义，请在应用层自行生成幂等键并在业务逻辑里去重，因为
  `/invoke` 本身不接受幂等键参数。
- **4xx（含 401/403/404/422）**：不要重试，先修正请求内容或凭证。
- **建议**：始终传入稳定的 `session_id`（首次调用留空拿到平台生成的 `session_id`
  后，后续都复用它），这样即使某次请求失败重试，至少落在同一个会话里，便于对账和
  审计（`GET /sessions/{id}/messages` 可以复查实际发生了什么）。

### 8.3 工作流提交侧的幂等键（`X-Idempotency-Key`）

这与上面“客户端主动重试 `/invoke`”是两回事：当一个 workflow 走到需要对外提交
（webhook）的 complete 步骤时，**平台自身**会为该次提交自动附带
`X-Idempotency-Key: <idempotency_key>:<step_id>` 请求头（`idempotency_key` 来自
workflow 启动时生成、贯穿整个会话的标识），并在 webhook 的 JSON body 中同时携带
`idempotency_key` 字段。

如果你的接收端（workflow 对接的下游业务系统）实现了 webhook 处理，**必须**依据这个
键做幂等去重：当 complete 步骤的 webhook 因超时/网络问题触发平台侧重试时，同一个
`X-Idempotency-Key` 会被重复发送，接收端应当识别并跳过重复处理（例如用它作为唯一键
写入一张“已处理提交”表，重复到达时直接返回成功而不重复执行业务逻辑）。

---

## 9. 嵌入式聊天 Widget（`static/widget.js`）

最快的集成方式：不写任何前端代码，直接把一个 `<script>` 标签贴进你自己的网页，即可
获得一个悬浮气泡 + 对话面板。源码位置：`static/widget.js`（纯 ES2017 IIFE，无构建步骤、
无外部依赖，由 `server/main.py` 的静态文件挂载在 `/widget.js` 提供）。完整可运行的示例
页面见 `examples/widget-demo.html`。

### 9.1 嵌入代码

```html
<script src="https://your-hlab-host/widget.js"
        data-agent-id="agent_xxx" data-api-key="ak_invoke_scoped"
        data-title="在线客服" data-color="#1677ff" data-position="right"></script>
```

### 9.2 配置属性

| 属性 | 是否必填 | 说明 |
|---|---|---|
| `data-agent-id` | 必填 | 目标 Agent id |
| `data-api-key` | 必填 | **仅 `invoke` 作用域**的 API Key（见下方安全提示） |
| `data-host` | 可选 | 后端地址，缺省时取该 `<script>` 标签自身的 origin |
| `data-title` | 可选 | 面板标题，默认「AI 助手」 |
| `data-color` | 可选 | 主题色（气泡/头部/发送按钮），默认 `#1677ff` |
| `data-position` | 可选 | `right`（默认）或 `left` |
| `data-user-id` | 可选 | 透传为 `InvokeRequest.user_id` 的终端用户 id |

Widget 内部实现要点：渲染在 **Shadow DOM** 内（样式与宿主页面完全隔离）；`session_id`
以 `hlab_widget_session_<agent_id>` 为 key 存在 `localStorage`，面板头部提供「新会话」
按钮清空重来；请求走 `POST {host}/api/v1/invoke/stream`，SSE 事件解析逻辑与
`console/src/pages/PlaygroundPage.tsx` 一致（`answer_delta` 增量渲染、`answer_reset`
丢弃已流式内容、`answer` 权威替换、`done` 落地 `session_id` 与 followup 按钮、`error`
渲染可重试的错误气泡）；所有来自服务端或用户输入的文本一律通过 `textContent` 插入
DOM，不使用 `innerHTML`，避免 XSS。

### 9.3 安全提示

Widget 嵌入到公开网页后，`data-api-key` 会出现在页面源码里，任何访问者都能看到并复制
这个 Key。因此**必须**：

1. 只使用 `invoke` 作用域的 Key（创建方式见本文档第 1 节），绝不要把带 `manage` 作用域
   的 Key 用在 Widget 上——那意味着任何看到页面源码的人都能管理你的 Agent/知识库/工具。
2. 把 `AEZAB_CORS_ORIGINS` 设置为你实际托管该网页的域名（而不是默认的 `*`）——这是唯一
   能阻止其他网站的浏览器脚本盗用这个 Key 发起调用的手段（Key 本身不受限制来源）。
3. 依赖内置的 `/invoke` 限流（`RATE_LIMIT_PER_MINUTE`）兜底防刷；一旦发现某个 Key 被
   滥用，直接在控制台禁用/轮换该 Key 即可，不影响其他 Widget 部署。

更完整的生产部署清单（CORS、反向代理 SSE 配置、单进程限制等）见
[`docs/deployment.md`](./deployment.md)。
