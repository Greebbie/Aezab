import { useEffect, useMemo, useRef } from 'react';
import ReactFlow, {
  Background, Controls, MiniMap, MarkerType, useNodesState, BaseEdge, EdgeLabelRenderer, getSmoothStepPath,
  type Connection, type Edge, type Node, type EdgeProps, type ReactFlowInstance,
} from 'reactflow';
import { useTranslation } from 'react-i18next';
import 'reactflow/dist/style.css';
import type { WorkflowStep } from '../../types';
import WorkflowNode from './WorkflowNode';
import { workflowRoutes, type WorkflowRoute } from './graph';

interface WorkflowCanvasProps {
  steps: WorkflowStep[];
  onSelectStep: (step: WorkflowStep | null) => void;
  selectedStepId: string | null;
  onConnect: (connection: Connection) => void;
  onSelectRoute: (route: WorkflowRoute) => void;
}

const nodeTypes = { workflowStep: WorkflowNode };

function RouteEdge(props: EdgeProps) {
  const [path, x, y] = getSmoothStepPath(props);
  return <>
    <BaseEdge path={path} markerEnd={props.markerEnd} style={props.style} />
    <EdgeLabelRenderer>
      <button className="nodrag nopan" onClick={(event) => { event.stopPropagation(); props.data.onSelect(); }} style={{
        position: 'absolute', transform: `translate(-50%, -50%) translate(${x}px, ${y + props.data.labelOffset}px)`,
        pointerEvents: 'all', color: props.data.color, background: '#fff', border: '1px solid #edf0f4',
        borderRadius: 3, fontSize: 12, padding: '2px 5px', cursor: 'pointer',
      }}>{props.label}</button>
    </EdgeLabelRenderer>
  </>;
}

const edgeTypes = { route: RouteEdge };

export default function WorkflowCanvas({ steps, onSelectStep, selectedStepId, onConnect, onSelectRoute }: WorkflowCanvasProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const flowRef = useRef<ReactFlowInstance | null>(null);
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(() => { void flowRef.current?.fitView({ padding: 0.15 }); });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);
  const ordered = useMemo(() => [...steps].sort((a, b) => a.order - b.order), [steps]);
  const routes = useMemo(() => workflowRoutes(steps), [steps]);
  const initialNodes = useMemo<Node[]>(() => {
    const levels = new Map<string, number>();
    const queue = ordered.length ? [ordered[0].id] : [];
    if (queue.length) levels.set(queue[0], 0);
    for (let index = 0; index < queue.length; index++) {
      const source = queue[index];
      routes.filter((route) => route.source === source).forEach((route) => {
        if (!levels.has(route.target)) {
          levels.set(route.target, levels.get(source)! + 1);
          queue.push(route.target);
        }
      });
    }
    const rows = new Map<number, WorkflowStep[]>();
    ordered.forEach((step, index) => {
      const level = levels.get(step.id) ?? index;
      rows.set(level, [...(rows.get(level) || []), step]);
    });
    return ordered.map((step, index) => {
      const level = levels.get(step.id) ?? index;
      const row = rows.get(level)!;
      return {
        id: step.id, type: 'workflowStep',
        position: { x: (row.indexOf(step) - (row.length - 1) / 2) * 340 + 360, y: level * 150 + 30 },
        data: { step, index, total: steps.length }, selected: selectedStepId === step.id,
      };
    });
  }, [ordered, routes, selectedStepId, steps.length]);
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  useEffect(() => {
    setNodes((current) => initialNodes.map((node) => ({
      ...node, position: current.find((old) => old.id === node.id)?.position || node.position,
    })));
  }, [initialNodes, setNodes]);

  const edges = useMemo<Edge[]>(() => routes.map((route) => {
    const condition = route.rule?.condition;
    const color = route.kind === 'failure' ? '#cf1322' : condition ? '#ad6800' : '#1677ff';
    const label = condition
      ? `${condition.field} ${condition.op} ${Array.isArray(condition.value) ? condition.value.join(', ') : String(condition.value)}`
      : t(`workflows.graph.${route.kind}`);
    const parallel = routes.filter((item) => item.source === route.source && item.target === route.target && (item.kind === 'failure') === (route.kind === 'failure'));
    return {
      id: route.id, source: route.source, target: route.target,
      sourceHandle: route.kind === 'failure' ? 'failure' : 'success',
      type: 'route', label,
      data: { color, labelOffset: (parallel.indexOf(route) - (parallel.length - 1) / 2) * 28, onSelect: () => onSelectRoute(route) },
      style: { stroke: color, strokeWidth: 2, strokeDasharray: route.kind === 'implicit' ? '5 4' : undefined },
      labelStyle: { fill: color, fontSize: 12 }, labelBgPadding: [5, 4],
      markerEnd: { type: MarkerType.ArrowClosed, color },
    };
  }), [routes, t, onSelectRoute]);

  return (
    <div ref={containerRef} style={{ height: 580, minHeight: 400, maxWidth: 'calc(100vw - 280px)', background: '#fafcfe', border: '1px solid #d9e2ec', borderRadius: 8 }}>
      <ReactFlow
        nodes={nodes} edges={edges} onNodesChange={onNodesChange}
        onInit={(instance) => { flowRef.current = instance; }}
        onConnect={onConnect} onNodeClick={(_, node) => onSelectStep(steps.find((step) => step.id === node.id) || null)}
        onEdgeClick={(_, edge) => { const route = routes.find((item) => item.id === edge.id); if (route) onSelectRoute(route); }}
        onPaneClick={() => onSelectStep(null)} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
        deleteKeyCode={null} fitView minZoom={0.25} maxZoom={1.5}
      >
        <Background color="#d4dde7" gap={20} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor="#91caff" style={{ width: 120, height: 80 }} />
      </ReactFlow>
    </div>
  );
}
