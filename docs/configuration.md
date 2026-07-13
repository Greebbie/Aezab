# 配置指南

> 面向已经把 Aezab 跑起来、需要配置模型、Agent、能力触发和知识库的用户。安装和快速上手见
> 根目录 [`README.md`](../README.md)。English version: [`docs/configuration_en.md`](./configuration_en.md)。

---

## 命名说明

Aezab 是当前的公开品牌名。`HlAB`（Headless AI Agent Builder）是历史代号——环境变量前缀
`AEZAB_`（`HLAB_` 作为向后兼容别名继续保留）、SDK 包名 `hlab-client`，以及部分代码注释/
文件名仍然使用 HlAB。两者指的是同一个项目，不是两个不同的产品。

## 核心概念

| 概念 | 说明 |
| --- | --- |
| Agent | 面向一个业务场景的运行单元，包含提示词、模型配置、能力绑定和运行策略。 |
| Capability | Agent 可使用的能力集合，包括知识库、工作流、工具和 Agent 协作。 |
| Knowledge | 文档和结构化知识源，支持上传、切分、索引、检索和引用返回。 |
| Workflow | 多步骤业务流程，可包含字段收集、文件上传、校验、工具调用和回调。 |
| Tool | 外部 HTTP API 或内置函数，通过 function calling 暴露给 Agent。 |

## 模型配置

Aezab 需要至少一个可用的 LLM。推荐做法是不去动 `.env` 里的 LLM 设置，直接打开控制台，跟着
首次运行向导连接一个云端模型——写入数据库的配置始终优先于 `.env` 默认值。也可以在控制台的
Model Configs 页面维护多套模型配置。

本地 Ollama（完全离线，需要先用 `docker compose --profile local-llm up -d` 启动 `ollama` 服务）：

```bash
AEZAB_LLM_PROVIDER=openai_compatible
AEZAB_LLM_BASE_URL=http://ollama:11434/v1
AEZAB_LLM_MODEL=qwen2.5:1.5b
```

DashScope：

```bash
AEZAB_LLM_PROVIDER=dashscope
AEZAB_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AEZAB_LLM_API_KEY=sk-your-key
AEZAB_LLM_MODEL=qwen-flash
```

其他 OpenAI-compatible 服务（MiniMax、DeepSeek、vLLM 等）配法相同，把 `base_url`/`model`
换成对应值即可。运行时优先级：`Agent 绑定的模型配置 > 租户默认模型配置 > .env 默认配置`。

## 配置 Agent

推荐配置顺序：

1. 在 **Agents** 创建 Agent，设置名称、描述、系统提示词和模型配置。
2. 在 **Knowledge** 创建知识源并上传文档。
3. 在 **Agents -> Capabilities** 绑定该 Agent 可使用的知识源。
4. 在 **Workflows** 配置业务流程，并绑定到 Agent。
5. 在 **Tools** 或 **Integrations -> Outbound Tools** 注册外部 API，并绑定到 Agent。
6. 在 **Playground** 测试对话、检索、工作流、工具调用和 trace。

**Agents -> Capabilities 是运行时能力的唯一主配置。** Integrations 页面中的绑定入口只是
快捷操作，最终仍写回同一套 Capabilities 配置。

## 能力触发逻辑

Aezab 使用 conversation-first runtime：Agent 已绑定的能力会转换为 function definitions，
由模型根据上下文自行判断是否调用。

| 能力 | 主要触发依据 |
| --- | --- |
| Knowledge | 知识源、domain、文档内容、检索结果。 |
| Workflow | 工作流名称、描述、步骤定义、可选 Agent-specific instruction。 |
| Tool | 工具名称、描述、参数 schema、返回 schema。 |
| Agent delegation | Agent 连接关系和目标 Agent 描述。 |

触发不稳定时优先调整能力自身的名称/描述/schema，而不是依赖 Agent-specific instruction。

## 知识库上传标准

上传文件会解析成文本，按 `chunk_size`/`chunk_overlap` 递归切分，进入 BM25、向量索引和
fast lookup；默认单文件上限 `AEZAB_KNOWLEDGE_MAX_UPLOAD_MB=50`。

- TXT/MD：UTF-8/GB18030/UTF-16，一段一个知识点，FAQ 建议写成「问题/答案」对。
- DOCX/PDF：使用真实文本，不要上传扫描图片；扫描件需先 OCR。
- CSV/XLSX：建议保留表头；表格会转成 `字段: 值` 形式的检索文本，比原样拼接更适合问答。
- 不建议直接上传：加密 PDF、复杂透视表、嵌入附件、只存在图片里的文字。

上线前建议在 **Knowledge -> 检索测试** 和 **Playground** 分别验证召回结果和最终回答。
