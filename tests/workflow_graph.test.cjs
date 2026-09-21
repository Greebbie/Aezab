// Run with: node tests/workflow_graph.test.cjs
// Use the console's existing TypeScript compiler; no additional test runtime.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../console/node_modules/typescript');
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../console/src/components/workflow/graph.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const exported = {};
new Function('exports', compiled)(exported);
const { editableRules, workflowRules, workflowRoutes, resolveStep } = exported;
const steps = [
  { id: 'collect', name: '1', order: 0, step_type: 'collect' },
  { id: 'route', name: 'Route', order: 1, step_type: 'decision', fallback_step_id: 'pause',
    next_step_rules: { rules: [{ condition: { field: 'route', op: 'eq', value: 'refund' }, goto_step: 'done' }] } },
  { id: 'done', name: 'Done', order: 2, step_type: 'complete' },
  { id: 'pause', name: 'Pause', order: 3, step_type: 'human_review' },
];
assert.deepEqual(workflowRules({ rules: [] }), []);
assert.equal(workflowRules(steps[1].next_step_rules).length, 1);
assert.equal(resolveStep(steps, '1').id, 'collect', 'Name must win over order');
assert.equal(resolveStep(steps, 'done').name, 'Done', 'Stable IDs must resolve');
assert.deepEqual(editableRules(steps, [{ condition: { field: 'route', op: 'in', value: 'refund, inquiry' }, goto_step: 'Done' }])[0], {
  condition: { field: 'route', op: 'in', value: ['refund', 'inquiry'] }, goto_step: 'done',
}, 'Legacy membership strings become editable tags without changing their meaning');
let routes = workflowRoutes(steps);
assert(routes.some((route) => route.source === 'route' && route.kind === 'implicit' && route.target === 'done'), 'No-match still follows order');
assert(routes.some((route) => route.source === 'route' && route.kind === 'failure' && route.target === 'pause'), 'Failure route must be visible');
assert(!routes.some((route) => route.source === 'done'), 'Completion is terminal, including middle steps');
steps[1].next_step_rules.rules.push({ condition: null, goto_step: 'pause' });
routes = workflowRoutes(steps);
assert(!routes.some((route) => route.source === 'route' && route.kind === 'implicit'), 'Explicit default replaces implicit order');
assert(routes.some((route) => route.source === 'route' && route.kind === 'default' && route.target === 'pause'));
steps[2].name = 'Renamed completion';
assert(workflowRoutes(steps).some((route) => route.source === 'route' && route.kind === 'condition' && route.target === 'done'), 'Renaming preserves stable-ID routes');
console.log('PASS: graph rules, stable IDs, precedence, implicit/default/failure routes, terminal completion');
