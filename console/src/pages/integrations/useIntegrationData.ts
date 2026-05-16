import { useCallback, useEffect, useState } from 'react';
import { agentApi, asrApi, auditApi, toolApi, workflowApi } from '../../api';
import type { Agent, AuditTraceSummary, Tool, Workflow } from '../../types';

interface IntegrationData {
  agents: Agent[];
  tools: Tool[];
  workflows: Workflow[];
  recentTraces: AuditTraceSummary[];
  asrStatus: Record<string, unknown> | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

export function useIntegrationData(): IntegrationData {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [recentTraces, setRecentTraces] = useState<AuditTraceSummary[]>([]);
  const [asrStatus, setAsrStatus] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [agentRes, toolRes, workflowRes, traceRes, asrRes] = await Promise.all([
        agentApi.list(),
        toolApi.list(),
        workflowApi.list(),
        auditApi.listTraces({ limit: 12 }),
        asrApi.getConfig(),
      ]);

      setAgents(agentRes.data || []);
      setTools(toolRes.data || []);
      setWorkflows(workflowRes.data || []);
      setRecentTraces(traceRes.data.items || []);
      setAsrStatus(asrRes.data || null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load integration data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  return { agents, tools, workflows, recentTraces, asrStatus, loading, error, reload };
}
