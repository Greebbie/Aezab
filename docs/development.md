# 开发者指南

> 面向准备修改代码、提交 PR 的贡献者。覆盖：提交前必须跑的本地检查、代码规范摘要。
> 面向用户的安装和使用说明见根目录 [`README_zh.md`](../README_zh.md)（中文）/
> [`README.md`](../README.md)（English）。

---

## 1. 提交前检查

修改代码后，在提交前至少跑一遍：

```bash
python -m pytest -q                  # 后端测试
npm --prefix console run build       # 前端 TypeScript 编译 + 打包，必须无报错
docker compose config --quiet        # 校验 docker-compose.yml 语法
```

`npx vite build`（在 `console/` 目录下）报 chunk size 警告是可接受的，但不能有编译错误。

## 2. 代码规范摘要

完整规范见仓库根目录 `CLAUDE.md` 的 “Code Cleanup Policy”，核心几条：

- 不留未使用的 import / 未被调用的函数；发现即删，不要注释掉。
- 不在已发布代码路径里留 `// TODO` 占位符——要么实现，要么删除。
- 前端所有 HTTP 调用必须走 `console/src/api.ts` 里的集中式客户端，页面组件不直接用
  `axios`。
- 两个函数做同一件事时只保留一个。
- 代码变更后同步更新 `CLAUDE.md` 和相关文档；和代码矛盾的文档比没有文档更糟。
- 每次改动都要能跑出一次干净的 `npx vite build`（chunk size 警告可以接受，报错不行）。

## 3. 静态资源同步（`static/` 目录）

`static/widget.js` 是独立维护的嵌入脚本，不属于 `console/dist` 的构建产物。根目录 README
里的前端构建命令用的是合并式拷贝（`cp -r dist/. ../static/`），不会清空 `static/`。**不要**
用 `rm -rf static/*` 或 `rsync --delete` 之类会先清空目标目录的方式同步构建产物，否则会连带
删掉 `widget.js`，导致线上 Widget 报 404。
