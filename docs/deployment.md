# HlAB 生产部署指南

> 面向要把 HlAB（Aezab）部署到生产环境的运维/后端工程师。覆盖：上线前检查清单、
> 单进程架构限制（关键）、反向代理下的 SSE 配置、Widget 部署安全、数据备份与迁移、
> 环境变量前缀。集成对接（SDK/API/Webhook/Widget 嵌入）见
> [`docs/integration.md`](./integration.md)；数据库迁移细节见
> [`docs/migrations.md`](./migrations.md)。
>
> 代码示例注释为英文，正文说明为中文。

---

## 1. 上线前检查清单

生产环境启动前，至少确认以下三项（`.env.example` / `README.md` 里默认值是为本地开发
准备的，**不能**直接照搬到生产）：

```bash
# 关闭开发免鉴权旁路——保持为 true 时，所有请求都会被当作 mock admin 放行
AEZAB_DISABLE_AUTH=false

# 精确列出允许跨域调用的前端/客户端来源，绝不要在生产用默认的通配符 "*"
# （见 server/main.py 的 _resolve_cors_settings：cors_origins="*" 时会强制
#  allow_credentials=False，这本身就说明 "*" 不是一个安全的生产配置）
AEZAB_CORS_ORIGINS=https://console.yourdomain.com,https://yourdomain.com

# 强随机 JWT 签名密钥——绝不要使用默认的 "change-me-in-production"
AEZAB_SECRET_KEY=<random-64-chars>
```

其余检查项：

- **管理员账号**：控制台使用真正的 JWT 登录（`console/src/AuthGate.tsx` +
  `console/src/pages/LoginPage.tsx`）。`AEZAB_DISABLE_AUTH=false`（当前默认值）时，
  首次打开控制台会引导创建第一个用户，该用户自动成为 `admin`（`POST
  /api/v1/auth/register`，见 `server/api/auth.py`）；之后新增用户需要由已登录的
  admin 创建（`POST /api/v1/auth/register-admin`）。务必给管理员账号设置强密码，并且
  不要把 `AEZAB_DISABLE_AUTH=true` 带到生产环境——那会让所有请求都被当作 mock admin
  放行，等于关闭了整个登录体系。
- **API Key 最小权限（scopes）**：`POST /api/v1/auth/api-keys` 创建 Key 时显式传
  `scopes`。对外暴露给客户端/Widget/第三方系统的 Key 只给 `invoke` 作用域；只有控制台
  管理员本人使用的 Key 才给 `manage` 作用域。空 `scopes` 列表等价于不限制（向后兼容旧
  Key），生产环境新建的 Key 都应该显式带上 scopes，不要留空。
- **API Key 明文存储（已知限制）**：Key 目前以哈希存储（`hash_api_key`，见
  `server/middleware/auth.py`），但 LLM/Embedding/ASR 等第三方供应商的凭证目前是明文
  存在数据库里（`llm_configs` 等表）。生产环境建议：数据库本身加密存储（磁盘加密 /
  云厂商托管数据库的静态加密），并定期轮换这些凭证；一旦怀疑泄露，立即在对应供应商
  控制台吊销并重新生成。
- **限流**：`AEZAB_RATE_LIMIT_PER_MINUTE`（默认 60，每个 API Key/用户/IP 每分钟请求数）
  按预期流量调整；见第 4 节关于 Widget 场景的限流依赖。

---

## 2. 单进程架构限制（关键，务必先读）

HlAB 的两个核心可靠性机制目前是 **进程内内存态**，不是跨进程共享的：

- **会话锁**（`server/engine/request_guard.py::session_lock`）：序列化同一个
  `session_id` 的并发 `/invoke` 调用，避免消息写入/`workflow_state` 更新交叉错乱。
- **幂等缓存**（`server/engine/request_guard.py` 的 `Idempotency-Key` 缓存）与
  **限流计数器**（`server/middleware/auth.py::enforce_rate_limit`）：都是进程内的
  `dict` + `threading.Lock`，没有 Redis 或其他跨进程共享后端。

`request_guard.py` 的模块级文档字符串原话：

```text
Both mechanisms are per-process, in-memory — they hold no state outside
this Python process. That is sufficient for a single-worker deployment or a
sticky-session (same client always routed to the same worker) multi-worker
deployment. If the platform ever moves to a non-sticky multi-worker or
multi-instance deployment, both primitives need a shared backend (e.g. a
Redis lock for `session_lock`, a Redis TTL cache for the idempotency store)
to keep the same guarantees across processes. That is the documented
upgrade path — not implemented in this wave.
```

**推论（务必遵守）**：

- **今天支持的部署形态**：单副本（Dockerfile 默认的 `uvicorn server.main:app`，单进程
  单 worker），或者带**粘性会话**（同一个客户端/会话固定路由到同一个后端实例）的多副本
  负载均衡。
- **不支持**：无粘性会话的多副本部署——不同请求可能落到不同进程，`session_lock` 形同
  虚设（并发写入可能交叉），`Idempotency-Key` 缓存和限流计数器在各个进程里各算各的
  （同一个 Key 在两个进程各能跑满一次限额，等于限流翻倍失效）。
- **今天绝对不要**用 `uvicorn ... --workers N`（N>1）启动本服务——单机内多 worker 本质
  上就是无粘性会话的多进程，同样会破坏上述两个机制。要提升单机吞吐，横向扩容多个
  Docker 副本 + 粘性会话负载均衡，而不是加 `--workers`。
- **已文档化的升级路径**：如果确实需要无粘性会话的多副本/多实例部署，需要把
  `session_lock` 换成 Redis 分布式锁、把幂等缓存和限流计数器换成 Redis TTL
  缓存/计数器——这是已知的下一步，本 wave 尚未实现。`docker-compose.yml` 里已经带了
  一个 `redis` 服务，但目前只是预留依赖，`request_guard.py` / `middleware/auth.py`
  尚未接入它。

---

## 3. 反向代理后的 SSE（`POST /invoke/stream`）

`/invoke/stream` 返回 `text/event-stream`，前端/Widget 依赖增量到达的字节而不是一次性
响应。反向代理如果做了响应缓冲，会导致客户端长时间收不到任何字节、直到整个响应结束才
一次性吐出——SSE 的实时性完全失效。

### nginx 示例

```nginx
location /api/v1/invoke/stream {
    proxy_pass         http://hlab_backend;
    proxy_http_version 1.1;

    # 关闭代理层缓冲——SSE 必须逐字节透传，不能等 buffer 攒满
    proxy_buffering off;
    proxy_cache off;

    # 后端应用自身也会发送 X-Accel-Buffering: no（见 server/api/invoke.py 的
    # invoke_stream 响应头），nginx 默认会尊重这个头；proxy_buffering off 是双保险
    add_header X-Accel-Buffering no;

    # 读超时要覆盖整条流水线可能的最长耗时，留一点余量。
    # 后端全局管道超时是 AEZAB_PIPELINE_TIMEOUT_SECONDS（默认 90 秒）
    proxy_read_timeout 120s;

    # keepalive，避免长连接被过早断开
    proxy_set_header Connection "";
}
```

要点：

- `proxy_buffering off`（或确认代理透传 `X-Accel-Buffering: no`）是最关键的一项——不做
  这个，SSE 事件会被攒批一次性发送，用户看不到打字机效果，甚至可能触发客户端超时。
- `proxy_read_timeout` 必须 **大于** `AEZAB_PIPELINE_TIMEOUT_SECONDS`（默认 90 秒），
  否则代理会在后端还没超时前就先掐断连接。
- HTTP/1.1 + keepalive：SSE 依赖长连接，`proxy_http_version 1.1` 是必须的（HTTP/1.0
  不支持分块传输编码下的流式响应）。
- 其他反向代理（Caddy、Traefik、云厂商 ALB/API Gateway）需要找到各自的等价配置项——
  核心诉求都是「关闭响应缓冲 + 放宽读超时」。

---

## 4. Widget 部署

嵌入代码（完整属性表见 `docs/integration.md` 第 9 节）：

```html
<script src="https://your-hlab-host/widget.js"
        data-agent-id="agent_xxx" data-api-key="ak_invoke_scoped"
        data-title="在线客服" data-color="#1677ff" data-position="right"></script>
```

Widget 是纯前端脚本，`data-api-key` 会原样出现在页面 HTML 源码里——**任何打开这个网页
的人都能在浏览器开发者工具里看到这个 Key 并复制走**。这不是一个 bug，而是所有「前端直
接调用 API」的嵌入式组件（Intercom、Crisp 等）共同的固有约束。因此必须组合以下三层
防护，而不是依赖 Key 本身保密：

1. **最小权限 Key**：只给 `invoke` 作用域，绝不要把 `manage` 作用域的 Key 用在 Widget
   上——泄露一个 `manage` Key 意味着任何人都能改你的 Agent 配置、删你的知识库。
2. **CORS 白名单是真正的防线**：把 `AEZAB_CORS_ORIGINS` 精确设置为托管这个 Widget 的
   客户网站域名（例如 `https://customer-site.com`），而不是 `*`。浏览器的同源策略会
   阻止其他域名的网页脚本用这个 Key 发起跨域请求——即使 Key 本身被人肉眼看到抄走，只要
   对方不能从一个被允许的 origin 发起请求，浏览器就会在预检阶段拦下来。这是唯一能
   限制「别的网站盗用这个 Key」的机制，Key 自身不带来源限制。
3. **限流兜底 + 及时轮换**：即使前两层都做到位，仍然依赖内置的 `/invoke` 限流
   （`AEZAB_RATE_LIMIT_PER_MINUTE`）防止单个 Key 被脚本刷爆。一旦监控/审计发现某个
   Widget 用的 Key 调用量异常，直接在控制台禁用旧 Key、生成新 Key、更新客户网页上的
   `data-api-key`，不影响其他 Agent 或其他 Widget 部署。

`/widget.js` 由 `server/main.py` 的静态文件挂载直接提供（`static/` 目录，与控制台前端
产物同级），不需要单独部署——只要 HlAB 服务本身可以被客户网站的浏览器访问到即可。

---

## 5. 数据：备份与迁移

- **内置零配置自动备份（`server/engine/backup.py`）**：服务启动后台自动每
  `AEZAB_BACKUP_INTERVAL_HOURS`（默认 24）小时把 SQLite 数据库、FAISS 向量索引、
  `asr_config.json`、`secret_key` 打包成 `./data/backups/aezab-backup-YYYYMMDD-
  HHMMSS.zip`，只保留最新 `AEZAB_BACKUP_KEEP`（默认 7）份，更旧的自动删除。SQLite
  部分用标准库 `sqlite3` 的在线备份 API（`Connection.backup`）而不是直接复制文件，
  避免复制到 WAL 模式下写到一半的文件。设 `AEZAB_BACKUP_INTERVAL_HOURS=0` 关闭定时
  任务。控制台「设置」页有对应的备份列表/立即备份/下载/删除入口（管理员权限，
  `GET/POST/DELETE /api/v1/backups`、`GET /api/v1/backups/{name}/download`）——
  ***强烈建议*** 运维定期把下载的备份文件另存到服务器之外的地方，`./data/backups/`
  本身和数据库存在同一块磁盘上，磁盘整体损坏时起不到异地容灾的作用。
- **SQLite（默认/小规模部署）**：数据文件在 `AEZAB_DATABASE_URL` 指向的路径（默认
  `./data/aezab.db`），Docker Compose 部署下落在 `aezab-data` volume 里。上面的自动
  备份已经覆盖了这个文件；如需手工复制，同样要用在线备份 API/`.backup` 命令，不要直接
  `cp` 正在写入的文件。
- **PostgreSQL（生产推荐）**：设置 `AEZAB_DATABASE_URL=postgresql+asyncpg://user:pass@host/db`
  切换后端；自动备份模块会跳过数据库文件（manifest 里标注 `db_type: "postgresql"`），
  只打包 FAISS 索引和本地配置文件——数据库本身仍需走标准的 `pg_dump`/`pg_basebackup`/
  云厂商托管备份方案，不是 HlAB 自身的职责。
- **向量索引重建**：FAISS 索引文件损坏、embedding 模型换了维度、或者数据库和索引不一致
  时，用 `POST /api/v1/vector-admin/rebuild` 从数据库里的 `knowledge_chunks` 全量重建
  索引（其余相关只读接口：`GET /vector-admin/stats`、`GET /vector-admin/health`、
  `POST /vector-admin/warmup`）。
- **数据库结构迁移**：服务启动时会自动运行迁移（`ensure_schema()`，基于 Alembic），
  不需要运维手工执行 `ALTER TABLE`。首次从旧版本（没有 `alembic_version` 表的既有部署）
  升级时的行为、以及如何在模型变更后生成新的迁移脚本，见
  [`docs/migrations.md`](./migrations.md)。

---

## 6. 环境变量前缀

- **`AEZAB_*` 是当前主前缀**（例如 `AEZAB_DATABASE_URL`、`AEZAB_CORS_ORIGINS`）。
- **`HLAB_*` 作为历史遗留别名继续被接受**（`server/config.py::env_field` /
  `env_str` 会依次尝试 `AEZAB_{name}` 再回退到 `HLAB_{name}`），用于项目改名前部署的
  平滑过渡。新部署一律使用 `AEZAB_*`；`docker-compose.yml` / `Dockerfile` 里也统一是
  `AEZAB_*` 优先、`HLAB_*` 兜底的写法。两者同时设置时 `AEZAB_*` 优先生效。

---

## 附：已知限制（生产前应知悉）

- API Key（用户认证用）本身以哈希存储；但 LLM/Embedding/ASR 供应商凭证目前明文存于
  数据库——见第 1 节的建议（磁盘加密 + 定期轮换）。
- 控制台前端认证是 JWT 登录 + 角色（`admin` / `editor` / `viewer`），已经是一个真实的
  多用户体系（`console/src/AuthGate.tsx`、`console/src/pages/LoginPage.tsx`、
  `server/api/auth.py`），不是密码锁占位方案；如果需要企业 SSO，可以把 Aezab 放在
  反向代理网关后面，或者在 `server/api/auth.py` 基础上接入 OAuth/SAML。
- 忘记管理员密码目前没有内置的「找回密码」流程：需要运维直接操作数据库（删除或重置
  `users` 表里对应记录的 `password_hash`，或整库重置），详见
  [`docs/troubleshooting.md`](./troubleshooting.md) 第「控制台打不开 / 一直
  401」条——操作前务必先备份 `./data/aezab.db`。
- 多租户隔离（`tenant_id`）目前主要在数据查询层面生效；跨租户的 JWT 校验强化是后续
  计划项，见项目根目录 `CLAUDE.md` 的 Future Optimization 部分。
