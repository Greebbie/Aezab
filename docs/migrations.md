# 数据库迁移（Alembic）

> Wave 5 / Workstream B。本文档介绍如何在模型变更后生成迁移脚本、迁移如何在启动时自动执行、
> SQLite 的 batch-mode 限制，以及 PostgreSQL 部署的注意事项。

## 背景

在 Wave 5 之前，本项目只依赖 SQLAlchemy 的 `Base.metadata.create_all()`。这只能**新增表**，
永远不能修改已存在表的列（增加/删除/改类型列），Wave 2 甚至为此手写过一次性的
`ALTER TABLE agents ADD COLUMN llm_config_id ...` 来变通。这种方式无法扩展：客户升级时一旦
遇到列级别的 schema 变更，服务就会启动失败或数据不一致。

Wave 5 引入 [Alembic](https://alembic.sqlalchemy.org/) 作为正式的迁移工具，`server/db_migrate.py`
的 `ensure_schema()` 替换了原来的 `create_all()` 调用，在应用启动（`server/main.py` 的
`lifespan`）时自动执行。

## 目录结构

```text
alembic.ini              # Alembic 配置（脚本位置等；数据库 URL 由 alembic/env.py 从
                          # server.config.settings.database_url 读取，不是从这里读取）
alembic/
  env.py                  # 异步迁移环境（async engine + connection.run_sync）
  script.py.mako          # 新迁移脚本的模板
  versions/
    0001_baseline.py       # 基线迁移：完整捕获 Wave 5 之前 create_all() 产生的全部 17 张表
server/
  db_migrate.py            # ensure_schema() —— 启动时的迁移入口
```

## 启动时自动执行（`ensure_schema`）

`server/db_migrate.py::ensure_schema()` 在每次进程启动时被调用（`server/main.py` 的
`lifespan`），会先探测数据库当前状态，然后走以下三种情形之一：

1. **全新数据库**（没有任何业务表）：走最快的开发路径 —— 直接
   `Base.metadata.create_all()`（比在空库上逐条回放所有历史迁移快得多），然后
   `alembic stamp head` 让 Alembic 的版本记录（`alembic_version` 表）与刚建好的表结构对齐。
2. **已存在的老数据库，但没有 `alembic_version` 表**（即所有 Wave 5 之前部署的数据库）：
   先把数据库**修复到 Wave 5 基线**（`0001_baseline.py` 完整捕获的 17 张表结构），再打标记。
   修复完整复刻旧版 lifespan 每次启动都会做的事：`create_all`（幂等，只补建缺失的表 ——
   例如上次启动于 Wave 3 之前的数据库缺少 `event_subscriptions`），加上历史遗留的
   `ALTER TABLE agents ADD COLUMN llm_config_id ...` 列补丁（列已存在时静默跳过，与旧代码
   行为一致）。修复完成后才执行 `alembic stamp head` 标记为最新 —— 如果不先修复就直接打
   标记，缺失的表/列会被永久冻结（Alembic 会认为 schema 已是最新，永远不再回头处理）。
   此后的迁移会正常按顺序应用。
3. **已存在 `alembic_version` 表**：执行真正的 `alembic upgrade head`，应用所有比当前记录
   更新的迁移。

三种情形都是幂等的：重复启动不会报错、不会重复建表。

```python
# 编程方式调用（例如脚本、测试）：
import asyncio
from server.db_migrate import ensure_schema

asyncio.run(ensure_schema())
```

## 模型变更后如何生成迁移

1. 修改 `server/models/*.py` 中的模型（新增字段、修改类型等）。
2. 生成迁移脚本（自动比对 `Base.metadata` 与当前数据库 schema 的差异）：

   ```bash
   # 针对开发用的 sqlite 库
   alembic revision --autogenerate -m "add xxx column to agents"
   ```

3. **打开生成的文件，人工检查**：确认 `upgrade()` / `downgrade()` 里的操作符合预期
   （Alembic 的自动比对不能识别列重命名，会生成"删列+加列"，需要手动改成
   `op.alter_column(..., new_column_name=...)`）。
4. 本地针对一个临时数据库文件跑一遍确认无误：

   ```bash
   AEZAB_DATABASE_URL=sqlite+aiosqlite:///./_verify.db alembic upgrade head
   ```

5. 提交迁移脚本。下次任意环境启动时，`ensure_schema()` 会自动应用它（情形 3）。

## SQLite 的 batch-mode 限制

SQLite 不支持大多数原地 `ALTER TABLE`（改列类型、删列、加约束等）。`alembic/env.py` 对
sqlite 方言始终开启了 `render_as_batch=True`：Alembic 会在后台把整张表重建一遍（新建临时表
→ 拷贝数据 → 删除旧表 → 改名）来模拟这些操作。这意味着：

- 生成迁移脚本时应使用 `with op.batch_alter_table("table_name") as batch_op:` 包裹列级操作
  （`alembic revision --autogenerate` 针对 sqlite 目标会自动这样生成）。
- 大表的 batch 迁移会短暂地把整张表复制一遍，请评估好维护窗口。

## PostgreSQL 说明

生产环境推荐 `AEZAB_DATABASE_URL=postgresql+asyncpg://user:pass@host/db`。PostgreSQL 原生支持
大部分 `ALTER TABLE` 操作，`render_as_batch` 只在 sqlite 方言下生效（见 `alembic/env.py`），
对 PostgreSQL 会直接执行标准 `ALTER TABLE` 语句，不会做整表重建。迁移脚本本身与数据库无关，
同一份迁移可以先在 sqlite 开发环境验证，再在 PostgreSQL 生产环境执行。

## 参考：环境变量

Alembic 从不读取 `alembic.ini` 里的 `sqlalchemy.url`（该值留空），而是始终使用应用自身的配置：

```bash
AEZAB_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/aezab
# 兼容旧前缀 HLAB_DATABASE_URL
```

这保证了手动执行 `alembic upgrade head` 时，永远和应用运行时连接的是同一个数据库。
