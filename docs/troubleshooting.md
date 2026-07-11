# 故障排查手册

> 面向非技术用户的自查手册。English version: [`docs/troubleshooting_en.md`](./troubleshooting_en.md)。
> 每条按「现象 → 原因 → 怎么办」组织。生产部署清单见 [`docs/deployment.md`](./deployment.md)；
> 外部系统对接见 [`docs/integration.md`](./integration.md)。

---

## 1. 对话报错 / 一直失败

**现象**：Playground 或你自己接入的 Agent 一直回复错误、超时，或者干脆没反应。

**先做这一步**：打开控制台左侧 **Health（系统健康）** 页，找到「LLM」这张卡片，点「重新
检测」（会实际发一次最小的模型请求去验证连通性，不是单纯看配置是否存在）。这张卡片会直接
给出人话结论和对应操作按钮：

| 检测结果 | 含义 | 怎么办 |
|---|---|---|
| `healthy` | 模型连接正常 | 如果对话还是失败，问题不在模型本身，看 Health 页里数据库/向量索引/熔断器几张卡片是否也正常 |
| `auth_error` | API Key 无效或已过期 | 点卡片上的「前往模型配置」按钮，回到模型供应商（通义千问 / 智谱 / MiniMax / OpenAI 等）控制台重新生成 Key，粘贴回来重新测试 |
| `rate_limited` | 调用频率受限或账户余额不足 | 去模型供应商账户页确认余额和限流额度，等待或充值后重试 |
| `unreachable` | 连不上模型服务地址 | 确认 `AEZAB_LLM_BASE_URL`（或模型配置里的 Base URL）拼写、协议（http/https）、端口是否正确；常见于国内机器直连海外 API 或内网机器访问不了公网，必要时配置代理或换成国内可直连的供应商 |
| `not_configured` | 还没有设置默认模型 | 去「模型配置」页新建一个配置并设为默认，或者直接走首次运行向导 |
| `error` | 其他调用失败 | 卡片上的详情文字会带具体报错，按提示处理；仍无法判断就去「模型配置」页手动点「测试连接」看完整报错 |

首次配置模型时，也可以直接走控制台的首次运行向导——向导第二步就是「测试连接」，效果和
Health 页的检测一致。

---

## 2. 首次上传知识库很慢，甚至像卡住了

**现象**：第一次在 Knowledge 页上传文档，进度条卡住很久，或者服务器日志一直没有反应。

**原因**：默认的本地 embedding 模型（`BAAI/bge-m3`）体积大约 2GB，**第一次**使用时会自动从
HuggingFace 下载到本地缓存（`aezab-model-cache` volume / `~/.cache/huggingface`），下载完成
后之后所有请求都会很快，不会重复下载。

**怎么办**：

- 确认 `.env` 里 `AEZAB_HF_ENDPOINT=https://hf-mirror.com` 没有被删掉或改错——这是国内可用的
  HuggingFace 镜像，没有它，国内网络环境下这次下载可能会很慢甚至失败。
- 用 `docker compose logs -f server` 观察下载进度（能看到 `Downloading` 相关的日志行），确认
  不是完全卡死，只是下载慢。
- **小内存机器（<4GB 可用内存）** 加载 embedding 模型时可能触发 OOM（进程被系统杀掉、容器
  意外重启）。建议至少给 `server` 容器预留 4GB 以上内存；内存实在紧张的话，可以把
  `AEZAB_EMBEDDING_PROVIDER` 换成 DashScope 等云端 embedding API，不在本机加载模型。

---

## 3. 换了 embedding 模型后检索变空 / 召回不到东西

**现象**：知识库明明上传了文档，换了 `AEZAB_EMBEDDING_MODEL` 之后，Playground / 检索测试
突然什么都搜不到了。

**原因**：不同 embedding 模型的向量维度不同（例如 bge-m3 是 1024 维，MiniLM 系列是 384
维）。换模型后，FAISS 索引里存的还是旧维度的向量，和新模型对不上，检索直接失效。

**怎么办**：在控制台「设置」/「向量管理」里点「重建索引」，或者直接调用：

```bash
curl -X POST http://localhost:8000/api/v1/vector-admin/rebuild \
  -H "X-API-Key: <你的Key>"
```

这会从数据库里已有的 `knowledge_chunks` 全量重新生成向量索引，不需要重新上传文档。重建期间
检索可能短暂不可用，文档不多的话通常几秒到几分钟就能完成。

---

## 4. 端口被占用

**现象**：`docker compose up -d --build` 或 `python -m uvicorn ...` 启动报错，类似
`address already in use` / `port is already allocated` / `[Errno 98] Address already in use`。

**原因**：8000 端口已经被别的程序占用了（可能是上一次没有正常退出的 Aezab 进程，也可能是别的
服务）。

**怎么办**：

- **Docker Compose**：编辑 `docker-compose.yml` 里 `server` 服务的 `ports` 映射，把
  `"8000:8000"` 改成例如 `"8080:8000"`（左边是宿主机端口，右边是容器内部端口不用改），然后
  用 `http://localhost:8080` 访问。
- **源码运行**：`uvicorn server.main:app --host 0.0.0.0 --port 8000` 把 `--port 8000` 改成
  别的端口，例如 `--port 8080`。
- 也可以先确认到底是谁占用了端口：Windows 用 `netstat -ano | findstr :8000`，找到对应
  `PID` 后用任务管理器结束，或者确认是不是自己之前启动的 Aezab 忘了关。

---

## 5. 控制台打不开 / 一直提示 401

**现象**：浏览器打开控制台地址，白屏、连接失败，或者登录后过一会儿又被弹回登录页、所有请求
都报 401。

**分情况处理**：

- **打不开（连接失败/超时）**：先确认后端进程/容器确实在运行——`docker compose ps` 看
  `server` 是不是 `Up` 状态，或者直接 `curl http://localhost:8000/health` 看有没有响应。
  没有响应就看 `docker compose logs -f server` 里的启动报错。
- **能打开但一直 401 / 反复跳回登录页**：登录状态（JWT）过期了，重新登录一次即可。如果刚重
  启过服务且**没有**设置固定的 `AEZAB_SECRET_KEY`，服务会用持久化在 `./data/secret_key` 里
  的密钥签名 token——只要这个文件没丢，重启不会导致所有人被登出；如果这个文件被删除或者换了
  一台机器部署（没有带上 `./data/` 目录），旧 token 会全部失效，所有人都需要重新登录，这是
  预期行为，不是 bug。
- **忘记管理员密码**：目前**没有**内置的「找回密码」邮件/短信流程，需要运维直接处理数据库。
  操作前**务必先备份** `./data/aezab.db`（参考第 8 节），然后二选一：
  1. 用 SQLite 工具打开 `./data/aezab.db`，删除 `users` 表里对应的用户记录，重启服务后
     用 `POST /api/v1/auth/register` 会把新创建的第一个用户自动设为管理员——但前提是
     `users` 表里已经没有任何用户了；如果还有别的用户，删除自己那条记录后需要请另一个管理员
     用 `POST /api/v1/auth/register-admin` 重新给你建号。
  2. 整库重置（`./data/aezab.db` 直接删除或替换成全新文件）——这会清空所有 Agent、知识库、
     会话等数据，只在测试环境或愿意从头开始时使用。

---

## 6. Webhook 收不到

**现象**：给「事件订阅（Subscriptions）」配置了一个 `url`，工作流也正常跑完了，但接收端一直
没收到回调。

**原因**：Aezab 默认拦截指向内网/回环地址的 webhook URL（`localhost`、`127.0.0.1`、
`10.x.x.x`、`192.168.x.x` 等私有网段），这是防止 SSRF（服务端请求伪造）攻击的安全措施——
默认情况下这类地址会被直接拒绝，投递也会被跳过。

**怎么办**：

- 如果你的接收端确实部署在内网/本机（例如本地联调、或者和 Aezab 部署在同一台内网机器上），
  在 `.env` 里显式设置 `AEZAB_ALLOW_INTERNAL_WEBHOOKS=true` 后重启服务，放行内网地址。
  **生产环境不建议长期打开**这个开关，联调完成后建议关闭或换成公网可达地址。
- 如果接收端本身就是公网地址却还是收不到，检查签名验证逻辑是否正确拒绝了请求（常见错误是对
  解析后再序列化的 JSON 验签，而不是对原始请求体字节验签）——详细说明和示例代码见
  [`docs/integration.md`](./integration.md) 第 7 节。
- 确认订阅的 `events` 字段包含你期望的事件类型（或者填 `["*"]` 订阅全部），以及订阅本身的
  `enabled` 没有被关掉。

---

## 7. 数据恢复

**现象**：需要回滚到之前的某个备份（误删数据、升级出问题、换机器部署等）。

**怎么办**：

1. 找到一份备份——可以是 `./data/backups/` 目录下的 `aezab-backup-YYYYMMDD-HHMMSS.zip`
   （每 24 小时自动生成，默认保留最新 7 份），也可以是之前从控制台「设置」页下载到本地的
   同名 zip 文件。
2. **停止 Aezab 服务**（`docker compose down` 或结束 `uvicorn` 进程）。
3. 解压这个 zip，按里面 `RESTORE.txt` 的说明操作：把解压出来的 `data/` 目录内容复制到你
   部署环境的 `./data/` 目录下，覆盖已有文件。
   - PostgreSQL 部署：这份备份**不包含**数据库本身，只包含 FAISS 向量索引和本地配置文件；
     数据库需要另外用 `pg_dump`/`pg_restore` 或云厂商的备份方案恢复。
4. **重启 Aezab 服务**。

平时建议定期把 `./data/backups/` 里的文件另存到服务器之外的地方（例如对象存储、异地磁盘）——
它和数据库存在同一块磁盘上，磁盘整体损坏时这份自动备份本身也会一起丢失。

---

## 8. `/health` 各组件含义速查

访问 `http://localhost:8000/health`（不带模型检测）能看到类似这样的结构：

```json
{
  "status": "ok",
  "version": "0.1.0",
  "components": {
    "database": { "status": "healthy" },
    "vector_store": { "status": "healthy", "backend": "faiss", "count": 128 },
    "circuit_breakers": { "status": "healthy", "total": 3, "open": 0 }
  }
}
```

| 组件 | 含义 | 异常时说明 |
|---|---|---|
| `database` | 数据库连通性（执行一次 `SELECT 1`） | `unhealthy` 通常是数据库文件损坏、权限问题，或 PostgreSQL 连不上 |
| `vector_store` | 向量索引文件是否存在、能查到多少条向量 | `not_initialized` 表示还没建过索引（正常，第一次上传文档前如此）；`unhealthy` 需要看错误详情，必要时按第 3 节重建索引 |
| `circuit_breakers` | 外部依赖（模型 API、工具 API 等）的熔断器状态 | `degraded` 且 `open` 不为 0，说明某个外部依赖持续失败被自动熔断了，稍等一段时间会自动尝试恢复，也可以先按第 1 节排查对应的模型/工具配置 |

顶层 `status` 是所有组件里最差状态的汇总：全部正常是 `ok`，任意一项不健康会变成
`degraded`。

**模型（LLM）连通性是可选的额外检测**，因为它会真实发一次请求，有实际费用，所以不在默认
响应里，只有显式带上 `check_llm=true` 才会执行（控制台 Health 页的 LLM 卡片已经自动带上
这个参数）：

```bash
curl "http://localhost:8000/health?check_llm=true"
```

结果会多出一个 `components.llm` 字段，`status` 取值就是第 1 节表格里的
`healthy` / `auth_error` / `rate_limited` / `unreachable` / `not_configured` / `error`。检测
结果会缓存 60 秒，避免监控系统高频轮询时把这个接口打成一个隐藏的「模型压测」；如果需要跳过
缓存立即重新检测（例如刚改完 Key），加上 `force=true`（控制台 Health 页的「重新检测」按钮
就是这么做的）：

```bash
curl "http://localhost:8000/health?check_llm=true&force=true"
```
