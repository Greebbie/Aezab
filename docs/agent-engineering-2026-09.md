# Aezab architecture and design

Design notes, 22 September 2026. Covers task execution, data access, context management and deployment constraints.

## Task execution

A small support team needs to answer a policy question, gather a repair request, decide which queue owns it, ask before submitting it, and find out whether the submission worked. An internal assistant needs the same mechanics for routine employee requests. Aezab should make that sequence easy to configure, inspect, and embed in an existing application.

The console manages agents, knowledge sources, workflow definitions, connections and tests. The runtime executes bounded tasks. A general conversation model chooses capabilities and explains results; deterministic code owns field validation, tenant scope, transitions and external effects. A dedicated decision model can supply a narrow semantic judgment when an ordinary condition is insufficient.

This favors finishing the existing runtime over introducing another orchestration framework. A graph is useful when its edges are the exact transitions the executor follows. Adding a second graph for every possible LLM tool choice would suggest a predictability that conversation-driven execution does not have.

## Product scope

Graphs, retrieval and model connections are common capabilities. Dify already documents branching, human input and execution inspection in its [workflow product](https://www.dify.ai/workflows). Flowise documents [queue-based production operation](https://docs.flowiseai.com/configuration/running-flowise-using-queue). These are useful comparisons, not evidence that Aezab matches every feature.

Aezab focuses on a small, independently deployable task runtime that fits into an existing business system: query a record, apply an inspectable decision, collect missing fields, request confirmation and return the execution result. Jev provides an optional semantic decision step within that flow.

Business data sources support typed local records and atomic imports, or a configured PostgreSQL relation with parameterized filters. Retrieval supplies policies and documentation; payment status and order ownership must be checked against business records. Business identity and write authorization stay with the receiving system. See [business data and database configuration](business-data.md).

This is a product judgment based on the current code and cited alternatives, not a claim about market share or an industry-wide shift away from RAG. For an enterprise deciding whether to adopt Aezab, one complete, reproducible task and a clear operating boundary are more persuasive than a longer feature list.

## Design references

| Evidence | Consequence for Aezab |
| --- | --- |
| TypeSafe introduced Jev in September 2026 as a structured decision model. Its API exposes Choice, Score and Noul rather than generated explanations. [Announcement](https://typesafe.ai/blog/introducing-system-one-models-and-jev), [API](https://docs.typesafe.ai/api) | Add an optional `decision` workflow step. Keep the existing chat-model adapter for conversation. Begin with Choice, because a workflow needs an explicit destination. |
| TypeSafe distinguishes distribution confidence from the probability of a particular option. Its model notes describe weaknesses in arithmetic, date comparison and large irrelevant state. [Confidence](https://docs.typesafe.ai/confidence), [model notes](https://docs.typesafe.ai/model-jaggedness/jev-1.13) | Keep exact rules in code. Send only named task fields; require both configured thresholds and a known option. Include `other`, and make failures take an explicit non-automatic path. |
| Anthropic describes context as a limited working set, including tools, retrieved evidence and conversation, and discusses compaction and just-in-time retrieval. [Context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | Account for tool schemas and arguments, retain valid tool/result groups, protect the current request, and load unsummarized history until a summary actually covers it. |
| LangGraph describes persistence and node-boundary checkpoints as part of durable execution. [Thinking in LangGraph](https://docs.langchain.com/oss/javascript/langgraph/thinking-in-langgraph) | Do not call a drawn workflow durable merely because its conversation state is in SQL. Process crashes between an external write and its receipt remain a separate problem. |
| Anthropic's evaluation guidance distinguishes the agent's execution path from the outcome. [Agent evaluations](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | Test destination, preserved state, external-call count and failure behavior. Model throughput and fluent answers alone do not prove task success. |

Provider performance claims are not Aezab benchmarks. A small live integration smoke run is recorded separately in [verification notes](verification-2026-09.md); it does not establish general accuracy, calibration or cost savings.

## Application integration

| Responsibility | Owner |
| --- | --- |
| Customer-facing task agent, selected knowledge, field collection, explicit workflow graph, tool call, task trace | Aezab |
| Employee identity, organization permissions, long-lived work context, cross-system coordination and authorization of consequential writes | Host application and business services |
| Conversation, explanation and general capability selection | Configured conversation model |
| Bounded classification of provided task facts | Optional Jev decision step |

The host application's backend calls `/api/v1/invoke` and receives `session_id`, `trace_id`, `workflow_status`, `workflow_card` and citations. The authenticated credential determines the tenant; `client_meta` carries correlation data and cannot establish identity or permission. The host application remains responsible for user authorization and write confirmations.

Use a stable session for one task. Treat streamed `answer_delta` as provisional until the final result; `answer_reset` can invalidate preceding text. Do not treat a natural-language answer as a business-system receipt. Synchronous invocation has process-local idempotency, and callbacks use a background dispatcher rather than a durable outbox. Neither provides a cross-process exactly-once guarantee. See [integration semantics](integration.md) and [deployment boundaries](deployment.md).

When a workflow returns a result, the conversational runtime returns that configured prompt or execution receipt directly. It does not request another model rewrite that could turn a classification result into a claim that someone has dispatched a worker or issued a refund.

## Context management

- **Instructions and capabilities:** operator-owned policy and the tools the current agent may use.
- **Evidence:** knowledge belonging to the authenticated tenant and the source IDs bound to this capability. Vector results are rechecked against SQL ownership before becoming citations.
- **Conversation working set:** the persisted summary plus rows after its watermark. A lagging background summary must not silently remove unsummarized messages. The input budget can still remove old history when necessary.
- **Task state:** collected workflow fields and decision metadata. These are stored separately from conversational summaries. A summary is not an authorization source.
- **Delegated context:** bounded information for a same-tenant target. Depth and cycle state come from the running parent, not client-supplied lineage.

Token counts remain estimates, not tokenizer-exact billing. When mandatory instructions, the current request, tool schemas and required tool-call structure cannot fit, the runtime emits a context-budget error. It does not silently rewrite the user's request. Trimming and overflow are visible in the trace. Context management is scoped to a session; cross-session personal memory is not provided.

Summary generation processes at most 100 rows and an estimated 6,000 input tokens per pass. Its watermark only advances across that batch. The stored summary is bounded and retained ahead of expendable old conversation; it is explicitly labeled as untrusted historical data. Oversized individual messages may be clipped for summarization, while the raw stored transcript remains available. Current business facts must be queried again, and a summary cannot establish ownership or permission.

Operators can page backward from the latest conversation and restore a selected session in Playground. Opening or refreshing it does not advance the workflow. Saved response cards preserve the response originally returned, including its workflow state.

## Operating limits

The `human_review` step is a requester-resumable pause. It has no authenticated staff approval queue. `requires_human_confirm` on a tool step is confirmation by that same requester; it does not grant a permission the business system would otherwise reject. The receiving business service must enforce authorization.

The runtime remains single-process for session locks, limits and request deduplication. Production horizontal scaling needs shared coordination and durable execution receipts. A webhook delivery failure keeps an honest retry state, but background notification delivery is not guaranteed after a process crash.

Planned work includes an authenticated operator handoff queue, durable task/side-effect receipts, and a labeled evaluation set for each customer's routing policy. Each addition requires defined API contracts and tests of execution outcomes.
