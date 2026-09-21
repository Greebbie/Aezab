# 开发者指南

> 面向准备修改代码、提交 PR 的贡献者。覆盖：提交前必须跑的本地检查、代码规范摘要。
> 面向用户的安装和使用说明见根目录 [`README_zh.md`](../README_zh.md)（中文）/
> [`README.md`](../README.md)（English）。

---

## 1. 提交前检查

开发环境使用 Python 3.10+、Node 20+；CI 使用 Python 3.11。先运行 `pip install -e '.[dev]' numpy faiss-cpu jieba` 和 `npm ci --prefix console`。公开测试使用临时 SQLite 和模拟的模型响应，不需要模型凭证，也不会下载 embedding 模型。修改文档解析或本地 embedding 功能时，再安装 `.[rag]`。

`console/package-lock.json` 是构建输入，需随依赖变动一起保留；Docker 的 `npm ci` 使用它。历史本地 E2E/demo 测试仍未公开，不要为运行公开回归而加入客户数据或私有凭证。真实供应商测试与离线回归分开报告。

修改代码后，在提交前至少跑一遍：

```bash
python scripts/verify_offline.py      # 与 CI 相同的公开离线回归
node tests/workflow_graph.test.cjs    # 图形配置与执行路由的契约
npm --prefix console run build       # 前端 TypeScript 编译 + 打包，必须无报错
test -f .env || cp .env.example .env  # 首次配置；保留已有 .env
docker compose config --quiet
```

`npx vite build`（在 `console/` 目录下）报 chunk size 警告是可接受的，但不能有编译错误。

## 2. 代码规范摘要

新测试需加入 `scripts/verify_offline.py` 的列表和 `.gitignore` 的公开测试允许列表。这个入口不会收集本地私有实验；可以附加 `-x` 或 `--collect-only` 等 pytest 参数。

代码规范：

- 不留未使用的 import / 未被调用的函数；发现即删，不要注释掉。
- 不在已发布代码路径里留 `// TODO` 占位符——要么实现，要么删除。
- 前端所有 HTTP 调用必须走 `console/src/api.ts` 里的集中式客户端，页面组件不直接用
  `axios`。
- 两个函数做同一件事时只保留一个。
- 代码变更后同步更新对应的使用文档和回归测试。
- 每次改动都要能跑出一次干净的 `npx vite build`（chunk size 警告可以接受，报错不行）。

## 3. 前端与嵌入脚本

源码运行时，后端优先提供 `console/dist` 中的控制台；执行 `npm --prefix console run build` 后即可访问，无需手动复制文件。Docker 构建会把同一份前端产物放到镜像的 `static/` 中。

`static/widget.js` 是独立维护的嵌入脚本，始终由 `/widget.js` 提供，不属于控制台构建产物。不要用清空 `static/` 的方式同步前端，以免删掉这个文件。
