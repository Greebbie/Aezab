# 业务数据与数据库配置

业务数据源用于查询订单、库存和客户记录，返回结构化字段与记录。知识库检索用于查询手册、政策及历史资料，返回相关文档片段和引用。两类数据在控制台分别配置；订单状态与访问权限以业务系统中的记录为准。

## 本地数据表

打开 **Business Data / 业务数据**，创建一个本地表。先定义字段及类型，再选择允许筛选的字段。以订单查询为例：

| 字段 | 类型 | 查询约束 |
| --- | --- | --- |
| order_id | string | 允许筛选，并设为必填条件 |
| status | string | 返回字段 |
| total | number | 返回字段 |
| paid | boolean | 返回字段 |

导入 UTF-8 CSV：

```csv
order_id,status,total,paid
ORD-1001,awaiting_dispatch,129.50,true
ORD-1002,shipped,69.00,true
```

CSV 单次导入上限为 2 MB、1,000 条记录；JSON 批量导入上限同为 1,000 条。整批数据通过校验后统一写入，校验失败不会写入部分记录。导入采用追加方式，不会按订单号自动合并。需要唯一订单约束、索引或处理大量记录时，应连接已有数据库，并在业务库中配置约束。

本地表已有记录后，字段结构不能直接修改。同一数据源的结构修改、记录写入与导入串行执行，锁在事务提交后释放，避免并发保存造成数据与 schema 不一致。该机制适用于单进程部署。

保存后，数据源会生成一个查询工具。到 **Agents → 编辑 Agent → Capabilities → Tools** 绑定它；工具名称带有数据源名称和唯一标识。它沿用现有工具调用及审计路径，也能用于工作流工具节点。

查询预览先验证数据和配置，例如：

```json
{"filters": {"order_id": "ORD-1001"}, "limit": 1}
```

预期返回一条记录，`status` 为 `awaiting_dispatch`。缺失必填条件、未知字段、类型错误或超过数据源上限时，请求会被拒绝。预览通过后，再在 Playground 检查 Agent 是否正确调用该工具、如实解释结果。空结果表示没有匹配记录，不代表系统已执行其他动作。

## PostgreSQL 数据源

管理员可以创建 PostgreSQL 数据源，填写连接 DSN、schema、表或视图，以及允许返回和筛选的字段。DSN 使用 `postgresql://`，保存在服务器端并加密；API 和控制台只显示是否已配置凭证。修改普通字段时不需要重新填写密码。

建议为 Agent 建立专门的业务视图与只读数据库账号，只授予必要的 `CONNECT`、schema `USAGE` 和指定表/视图 `SELECT` 权限。不要使用平台迁移账号或数据库超级用户查询业务。TLS、网络可达性和数据库权限由实际部署配置；托管数据库的连接要求仍需遵守。

执行器只生成选定列的等值筛选查询，值通过绑定参数传入。每次查询使用只读事务，连接与语句超时为 5 秒，默认最多返回 20 条，可设置为 1–200 条；多取一条只用于判断 `truncated`。当前不提供任意 SQL、连接查询、聚合报表或数据库写入。复杂业务逻辑可以收进只读视图；写入继续通过有业务授权的 HTTP API。

业务表若用租户列区分记录，可以设置 `tenant_column`。执行器会把经过授权的工具所属租户绑定进该条件；模型不能覆盖这个值。这个字段应与 Aezab 租户标识对应。若不配置租户列，数据源可查询该数据库账号可见的整个指定表/视图，因此应使用租户专属视图或数据库账号。

**终端用户的数据权限由业务系统校验。** 同一租户内，不同客户也可能具有不同的数据访问权限。必填订单号仅约束查询条件，无法证明订单归属。对外客服应通过已鉴权的业务 API 检查用户与订单关系，不应将共享租户的全量订单表直接开放给匿名聊天。

## 平台数据库配置

`AEZAB_DATABASE_URL` 指定 Aezab 的平台数据库，用于保存 Agent 配置及会话、工作流、审计和本地业务记录。该配置与业务数据源的连接独立。修改连接地址不会自动迁移 SQLite 中的已有数据，也不会更改业务数据源的目标数据库。

已有 PostgreSQL 服务时，在 `.env` 中设置：

```dotenv
AEZAB_DATABASE_URL=postgresql+asyncpg://aezab_app:<url-encoded-password>@db.example.internal:5432/aezab
```

新部署也可以使用仓库的 Compose 叠加文件。在 `.env` 设置一个随机、URL-safe 的 `AEZAB_POSTGRES_PASSWORD`，然后运行：

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build
```

叠加配置启动 PostgreSQL 17，数据库端口只在 Compose 网络内可见，数据保存到独立 volume，健康检查通过后应用才启动。平台账号需要创建和迁移表的权限；它与业务查询账号应分开。继续保存 `aezab-data` volume，其中仍有文件、向量索引和加密密钥。

切换数据库前，先备份并在空环境演练导入和恢复。内置自动备份只对 SQLite 生成数据库快照；使用 PostgreSQL 时还需要 `pg_dump` 或托管数据库自己的备份/PITR。参见 [PostgreSQL 备份文档](https://www.postgresql.org/docs/17/backup.html)。使用 PostgreSQL 时，应用仍须按单进程部署，会话锁、幂等缓存及回调队列均保存在进程内。详见 [部署说明](deployment.md)。

## API 与使用限制

`/api/v1/data-sources/` 提供数据源管理接口；`/{id}/records` 支持本地记录的增删改查和分页；`/{id}/records/import` 与 `/{id}/records/import-csv` 支持原子追加导入；`/{id}/query` 用于查询预览。完整 schema 见 `/docs`。数据源配置要求 `manage` 作用域，修改操作和查询预览至少需要 editor 角色，PostgreSQL 连接配置需要 admin 角色。Agent 通过数据源生成的工具查询数据，调用方式与其他工具一致。

本地表适用于小规模数据集，其 JSON 字段没有独立索引。PostgreSQL 等值查询应根据业务访问模式配置索引。两种配置均未完成企业级高可用或并发容量验证。性能测试应使用实际 schema、模型延迟和流量，并保留行数及必填查询条件限制。
