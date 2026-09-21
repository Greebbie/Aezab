# Put a semantic decision into a workflow

Jev is an optional cloud decision provider. Aezab's conversation model still answers users and chooses capabilities; Jev handles a configured, closed-set question inside an active workflow. This integration uses TypeSafe's [Choice API](https://docs.typesafe.ai/api), not an OpenAI-compatible chat endpoint.

## Configure the server

Add these values to the server's `.env`, then recreate the Compose service with `docker compose up -d` (or restart a source-run server):

```dotenv
AEZAB_TYPESAFE_ENABLED=true
TYPESAFE_API_KEY=<your-server-side-key>
AEZAB_TYPESAFE_MODEL=jev-1.13.0
AEZAB_TYPESAFE_TIMEOUT=10
```

The integration is disabled by default. The credential belongs on the server, never in a workflow JSON, browser build, or chat message. Only configured input fields are sent to `https://api.typesafe.ai/v1/systemone`; enabling the service introduces that cloud dependency even if Aezab and the conversation model are self-hosted. A disabled/unavailable provider takes the decision node's failure edge.

The version above was listed by TypeSafe on 22 September 2026. [Versioned IDs](https://docs.typesafe.ai/models) let an operator evaluate and deliberately upgrade a routing policy. Aliases can move; the actual returned model ID is retained in the decision trace.

## Build and inspect a route

1. Create a workflow and collect a text field such as `request`.
2. Add a **Decision (Jev)** step. Select `request` as its input; write the exact question and describe distinct options, for example `support`, `billing` and `other`.
3. Set a result field such as `route`. Add conditions `route eq support` and `route eq billing`, each with a target step. The graph shows the saved routes. Its dashed sequence edges show what happens if no condition matches.
4. Set both the confidence and selected-option probability thresholds. The initial `0.8` values are starting configuration, not a calibrated business guarantee.
5. Connect the failure outlet to a collect step that requests missing information, a manual pause, or a completion step without a webhook. Missing evidence, `other`, below-threshold judgments, malformed replies, provider errors and timeouts take this path.
6. Save, reopen the workflow, validate it, and test it in Playground. Inspect `workflow_decision` and `workflow_branch`: chosen option, probability distribution, confidence, resolved model, threshold outcome and destination are recorded. Raw decision input is not copied into that decision event; ordinary conversation/tool traces retain their existing behavior.

The graph edits the executor's `next_step_rules` and `fallback_step_id`. New edges use stable step IDs so renaming a label does not change the destination. Branch order matters: the first matching rule wins. A default rule belongs last. A **complete** step ends the workflow.

Use deterministic conditions for numbers, dates and exact business rules. An uncertain classifier should not calculate refund eligibility or authorize a payment. TypeSafe's own [model notes](https://docs.typesafe.ai/model-jaggedness/jev-1.13) discuss these limitations.

## Try the support triage example

[`examples/support-triage.workflow.json`](../examples/support-triage.workflow.json) can be submitted to `POST /api/v1/workflows` using an authenticated console/operator credential. Then bind it to an agent in **Capabilities → Workflows** and test that agent in Playground. The example only classifies and displays a destination; it does not create a real ticket or call a customer system.

With Jev disabled, submitting the request reaches **Manual handoff**. With a live credential, the configured decision can route to either informational completion. The manual pause can be resumed by the requester, so it must not be presented as manager approval.

For an offline, deterministic check of the same example and failure behavior:

```bash
pip install -e '.[dev]' numpy faiss-cpu jieba
python -m pytest tests/test_decision_service.py tests/test_workflow_decisions.py tests/test_workflow_graph.py tests/test_context_budget.py tests/test_runtime_resource_scope.py -q
```

The regression suite mocks provider responses; it tests the API contract, explicit field selection, malformed data, thresholds, disabled service, timeouts, fallback execution, confirmation and branch persistence. It is not a live model evaluation.

For a bounded live smoke test, set `MINIMAX_API_KEY` and `TYPESAFE_API_KEY` in the process environment, then run:

```bash
python scripts/verify_agent_workflows.py
```

The script uses synthetic support requests and a temporary SQLite database. It permits at most six MiniMax and twelve Jev requests, writes sanitized observations under the ignored `data/verification/` directory, and creates no external tickets. `--runtime-only` reruns just conversation and native workflow invocation. The workflow check requires the final answer to equal the actual configured receipt, not merely report a completed status. See the [September verification record](verification-2026-09.md) for the measured scope and limits.

Before automating a real queue, label representative requests including mixed intents, insufficient information and adversarial instructions. Measure correct routing, automatic-route coverage, inappropriate automatic actions, fallback rate, latency and cost separately. Choose thresholds from those results, and keep the data/policy/model version with the report. Probability confidence is not permission to perform an action.
