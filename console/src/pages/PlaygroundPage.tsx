import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  Button,
  Collapse,
  Empty,
  Input,
  InputNumber,
  List,
  message,
  Modal,
  Select,
  Space,
  Spin,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  Upload,
} from 'antd';
import {
  AudioOutlined,
  CheckCircleOutlined,
  ClearOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  SendOutlined,
  UploadOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { agentApi, invokeApi, auditApi, asrApi } from '../api';

const { TextArea } = Input;
const { Text, Paragraph } = Typography;

/* ── Types ────────────────────────────────────────────── */

interface WorkflowCardData {
  step_name: string;
  step_type: string;
  prompt: string;
  fields?: Array<{
    name: string;
    label: string;
    field_type?: string;
    required?: boolean;
    placeholder?: string;
    options?: Array<{ label: string; value: string }>;
    file_config?: { max_size_mb?: number; allowed_extensions?: string[] };
  }>;
  current_step: number;
  total_steps: number;
  collected_data?: Record<string, any>;
  webhook_result?: { ok?: boolean; status?: number; error?: string };
}

interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
  citations?: Citation[];
  followups?: string[];
  traceId?: string;
  metadata?: Record<string, any>;
  isStreaming?: boolean;
  error?: boolean;
  workflowCard?: WorkflowCardData;
  workflowStatus?: string;
  skillInfo?: { skill_id?: string; skill_name?: string; skill_type?: string; delegated_to?: string };
}

interface Citation {
  source_id: string;
  source_name: string;
  content_snippet: string;
  page?: number | null;
  paragraph?: number | null;
  line_start?: number | null;
  line_end?: number | null;
  score?: number | null;
}

interface TraceEvent {
  event_type: string;
  timestamp?: string;
  latency_ms?: number;
  event_data?: any;
  retrieval_hits?: any;
  llm_meta?: any;
  tool_meta?: any;
  trace_id?: string;
}

/* ── Constants ────────────────────────────────────────── */

const EVENT_COLORS: Record<string, string> = {
  user_input: 'blue',
  intent: 'cyan',
  retrieval: 'green',
  llm_call: 'purple',
  tool_call: 'orange',
  workflow_step: 'geekblue',
  response: 'blue',
  escalation: 'red',
  error: 'red',
  risk_block: 'red',
  skill_route: 'magenta',
  skill_dispatch: 'volcano',
  delegation: 'gold',
  conversational_init: 'cyan',
  pre_retrieval: 'green',
  query_rewrite: 'lime',
};

/* ── Component ────────────────────────────────────────── */

export default function PlaygroundPage() {
  /* state — agents */
  const [agents, setAgents] = useState<any[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string | undefined>(undefined);
  const [agentsLoading, setAgentsLoading] = useState(false);

  /* state — chat */
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [sending, setSending] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [voicePanelOpen, setVoicePanelOpen] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [useStreaming, setUseStreaming] = useState(true);

  /* state — workflow form */
  const [workflowFormData, setWorkflowFormData] = useState<Record<string, any>>({});

  /* state — trace panel */
  const [tracePanelOpen, setTracePanelOpen] = useState(true);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [traceLoading, setTraceLoading] = useState(false);
  const [activeCitations, setActiveCitations] = useState<Citation[]>([]);

  /* refs */
  const chatEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordingChunksRef = useRef<Blob[]>([]);
  const recordingStreamRef = useRef<MediaStream | null>(null);
  const recordingTimerRef = useRef<number | null>(null);

  /* ── Load agents on mount ──────────────────────────── */

  useEffect(() => {
    setAgentsLoading(true);
    agentApi
      .list()
      .then((res) => {
        const list = Array.isArray(res.data) ? res.data : [];
        setAgents(list);
        if (list.length > 0 && !selectedAgentId) {
          setSelectedAgentId(list[0].id);
        }
      })
      .catch(() => message.error('Failed to load agents'))
      .finally(() => setAgentsLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /* ── Window resize → auto-hide trace panel on narrow screens ── */

  useEffect(() => {
    const onResize = () => {
      if (window.innerWidth < 900) setTracePanelOpen(false);
    };
    window.addEventListener('resize', onResize);
    onResize();
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    return () => {
      if (recordingTimerRef.current !== null) {
        window.clearInterval(recordingTimerRef.current);
      }
      recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  /* ── Auto scroll ───────────────────────────────────── */

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  /* ── Fetch trace for a given traceId ───────────────── */

  const loadTrace = useCallback(async (traceId: string) => {
    setTraceLoading(true);
    try {
      const res = await auditApi.getTrace(traceId);
      const data = Array.isArray(res.data) ? res.data : [res.data];
      setTraceEvents(data);
    } catch {
      message.error('Failed to load trace data');
    } finally {
      setTraceLoading(false);
    }
  }, []);

  /* ── New Session ───────────────────────────────────── */

  const handleNewSession = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setMessages([]);
    setSessionId(null);
    setTraceEvents([]);
    setActiveCitations([]);
    setWorkflowFormData({});
    setSending(false);
  };

  /* ── Send via SSE streaming ────────────────────────── */

  const sendStreaming = async (userMessage: string, formData?: Record<string, any>) => {
    const controller = new AbortController();
    abortRef.current = controller;

    /* Append a placeholder assistant message for streaming */
    const streamingMsg: ChatMessage = {
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      isStreaming: true,
    };
    setMessages((prev) => [...prev, streamingMsg]);

    try {
      const body: Record<string, any> = {
        agent_id: selectedAgentId!,
        message: userMessage,
        tenant_id: 'default',
      };
      if (sessionId) body.session_id = sessionId;
      if (formData) body.form_data = formData;

      const response = await fetch('/api/v1/invoke/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('ReadableStream not supported');

      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';
      let currentData = '';
      let finalContent = '';
      let finalCitations: Citation[] = [];
      let finalFollowups: string[] = [];
      let finalTraceId: string | undefined;
      let finalMetadata: Record<string, any> | undefined;
      let finalWorkflowCard: WorkflowCardData | undefined;
      let finalWorkflowStatus: string | undefined;
      let finalSkillInfo: ChatMessage['skillInfo'] | undefined;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        /* Keep the last incomplete line in the buffer */
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
            currentData = '';
          } else if (line.startsWith('data: ')) {
            currentData = line.slice(6);
          } else if (line === '' && currentEvent && currentData) {
            /* Complete SSE event pair received */
            try {
              const parsed = JSON.parse(currentData);

              if (currentEvent === 'answer') {
                finalContent = parsed.content || '';
                setMessages((prev) => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last && last.isStreaming) {
                    updated[updated.length - 1] = { ...last, content: finalContent };
                  }
                  return updated;
                });
              } else if (currentEvent === 'status') {
                /* Update streaming message with status */
                const stage = parsed.stage || 'processing';
                setMessages((prev) => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last && last.isStreaming && !last.content) {
                    const stageLabel =
                      stage === 'retrieval' ? 'Retrieving knowledge...' : 'Processing...';
                    updated[updated.length - 1] = { ...last, content: stageLabel };
                  }
                  return updated;
                });
              } else if (currentEvent === 'done') {
                finalTraceId = parsed.trace_id;
                finalCitations = parsed.citations || [];
                finalFollowups = parsed.followups || [];
                finalMetadata = parsed.metadata;
                finalWorkflowCard = parsed.workflow_card;
                finalWorkflowStatus = parsed.workflow_status;
                finalSkillInfo = parsed.skill_info || undefined;
                if (parsed.session_id) setSessionId(parsed.session_id);
              } else if (currentEvent === 'error') {
                const errorMsg = parsed.error_msg || parsed.detail || 'Unknown error';
                setMessages((prev) => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last && last.isStreaming) {
                    updated[updated.length - 1] = {
                      ...last,
                      content: errorMsg,
                      isStreaming: false,
                      error: true,
                    };
                  }
                  return updated;
                });
                message.error(errorMsg);
              }
            } catch {
              /* ignore malformed JSON */
            }
            currentEvent = '';
            currentData = '';
          }
        }
      }

      /* Finalize the streaming message */
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last && last.isStreaming) {
          updated[updated.length - 1] = {
            ...last,
            content: finalContent || last.content || 'No response received.',
            isStreaming: false,
            citations: finalCitations,
            followups: finalFollowups,
            traceId: finalTraceId,
            metadata: finalMetadata,
            workflowCard: finalWorkflowCard,
            workflowStatus: finalWorkflowStatus,
            skillInfo: finalSkillInfo,
          };
        }
        return updated;
      });

      /* Show citations in panel */
      if (finalCitations.length > 0) setActiveCitations(finalCitations);

      /* Auto-load trace */
      if (finalTraceId) loadTrace(finalTraceId);
    } catch (err: any) {
      if (err.name === 'AbortError') return;
      const errorText = err.message || 'Streaming request failed';
      message.error(errorText);
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last && last.isStreaming) {
          updated[updated.length - 1] = {
            ...last,
            content: errorText,
            isStreaming: false,
            error: true,
          };
        }
        return updated;
      });
    } finally {
      abortRef.current = null;
    }
  };

  /* ── Send via sync invoke (fallback) ───────────────── */

  const sendSync = async (userMessage: string, formData?: Record<string, any>) => {
    try {
      const body: Record<string, any> = {
        agent_id: selectedAgentId!,
        message: userMessage,
        tenant_id: 'default',
      };
      if (sessionId) body.session_id = sessionId;
      if (formData) body.form_data = formData;

      const res = await invokeApi.send(body);
      const data = res.data;

      setSessionId(data.session_id);

      const assistantMsg: ChatMessage = {
        role: 'assistant',
        content: data.short_answer || '',
        timestamp: Date.now(),
        citations: data.citations || [],
        followups: data.suggested_followups || [],
        traceId: data.trace_id,
        metadata: data.metadata,
        workflowCard: data.workflow_card,
        workflowStatus: data.workflow_status,
        skillInfo: data.skill_info || undefined,
      };
      setMessages((prev) => [...prev, assistantMsg]);

      if (data.citations?.length > 0) setActiveCitations(data.citations);
      if (data.trace_id) loadTrace(data.trace_id);
    } catch (err: any) {
      const errorText =
        err.response?.data?.detail || err.message || 'Request failed';
      message.error(errorText);
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: errorText,
          timestamp: Date.now(),
          error: true,
        },
      ]);
    }
  };

  /* ── Send payload (shared by text input & workflow actions) ── */

  const sendPayload = async (text: string, formData?: Record<string, any>) => {
    if (!selectedAgentId) {
      message.warning('Please select an agent first');
      return;
    }

    const userMsg: ChatMessage = { role: 'user', content: text, timestamp: Date.now() };
    setMessages((prev) => [...prev, userMsg]);
    setSending(true);

    try {
      if (useStreaming) {
        await sendStreaming(text, formData);
      } else {
        await sendSync(text, formData);
      }
    } finally {
      setSending(false);
    }
  };

  /* ── Send handler (text input) ───────────────────── */

  const handleSend = async () => {
    const text = inputValue.trim();
    if (!text) return;
    setInputValue('');
    await sendPayload(text);
  };

  const appendTranscript = (text: string) => {
    setInputValue((prev) => (prev.trim() ? `${prev.trim()}\n${text}` : text));
    setVoicePanelOpen(false);
  };

  const getErrorMessage = (err: any) => {
    return err?.response?.data?.detail || err?.response?.data?.message || err?.message || 'unknown error';
  };

  const transcribeAudioFile = async (file: File) => {
    setTranscribing(true);
    try {
      const res = await asrApi.transcribe(file);
      const transcript = String(res.data?.text || '').trim();
      if (!transcript) {
        message.warning('No transcript returned from ASR');
        return;
      }
      appendTranscript(transcript);
      message.success('Audio transcribed');
    } catch (err: any) {
      message.error('Audio transcription failed: ' + getErrorMessage(err));
    } finally {
      setTranscribing(false);
    }
  };

  const handleAudioUpload = async (file: File) => {
    await transcribeAudioFile(file);
    return Upload.LIST_IGNORE;
  };

  const clearRecordingTimer = () => {
    if (recordingTimerRef.current !== null) {
      window.clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
  };

  const stopRecordingStream = () => {
    recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
    recordingStreamRef.current = null;
  };

  const handleRecordedAudio = async (blob: Blob) => {
    if (!blob.size) {
      message.warning('No recorded audio captured');
      return;
    }
    const file = new File([blob], `voice-${Date.now()}.webm`, { type: blob.type || 'audio/webm' });
    await transcribeAudioFile(file);
  };

  const startRecording = async () => {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      message.error('Browser recording is not available on this page');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      recordingChunksRef.current = [];
      recordingStreamRef.current = stream;

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) recordingChunksRef.current.push(event.data);
      };

      recorder.onstop = () => {
        clearRecordingTimer();
        setRecording(false);
        setRecordingSeconds(0);
        stopRecordingStream();
        const blob = new Blob(recordingChunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        recordingChunksRef.current = [];
        void handleRecordedAudio(blob);
      };

      mediaRecorderRef.current = recorder;
      recorder.start();
      setRecording(true);
      setRecordingSeconds(0);
      recordingTimerRef.current = window.setInterval(() => {
        setRecordingSeconds((seconds) => seconds + 1);
      }, 1000);
    } catch (err: any) {
      stopRecordingStream();
      message.error('Recording failed: ' + getErrorMessage(err));
    }
  };

  const stopRecording = () => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === 'inactive') return;
    recorder.stop();
  };

  const formatRecordingTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
      .toString()
      .padStart(2, '0');
    const secs = (seconds % 60).toString().padStart(2, '0');
    return `${mins}:${secs}`;
  };

  /* ── Workflow form submit ────────────────────────── */

  const handleWorkflowSubmit = async (msgIdx: number) => {
    const card = messages[msgIdx]?.workflowCard;
    if (!card?.fields) return;

    for (const field of card.fields) {
      if (field.required && (workflowFormData[field.name] === undefined || workflowFormData[field.name] === '')) {
        message.warning(`Please fill in: ${field.label}`);
        return;
      }
    }

    const fd = { ...workflowFormData };
    setWorkflowFormData({});
    await sendPayload('[Form Submitted]', fd);
  };

  /* ── Workflow confirm / cancel ───────────────────── */

  const handleWorkflowConfirm = async () => {
    setWorkflowFormData({});
    await sendPayload('确认');
  };

  const handleWorkflowCancel = async () => {
    setWorkflowFormData({});
    await sendPayload('取消');
  };

  /* ── Handle followup click ─────────────────────────── */

  const handleFollowup = (question: string) => {
    setInputValue(question);
  };

  /* ── Handle enter key ──────────────────────────────── */

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!sending) handleSend();
    }
  };

  /* ── Active workflow card index (only the latest waiting_input card is interactive) ── */

  const activeWorkflowIdx = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'user') return -1;
      if (messages[i].workflowCard && messages[i].workflowStatus === 'waiting_input') return i;
    }
    return -1;
  })();

  /* ── Render workflow card ────────────────────────── */

  const renderWorkflowCard = (msg: ChatMessage, idx: number) => {
    const card = msg.workflowCard;
    if (!card) return null;

    const isCompleted = msg.workflowStatus === 'completed' || card.step_type === 'complete';
    const isConfirm = card.step_type === 'confirm';
    const isActive = idx === activeWorkflowIdx && !sending;

    /* ── Completed state ── */
    if (isCompleted) {
      return (
        <div
          style={{
            marginTop: 8,
            padding: '12px 16px',
            background: '#f6ffed',
            border: '1px solid #52c41a',
            borderRadius: 8,
          }}
        >
          <Space>
            <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 18 }} />
            <Text strong style={{ color: '#52c41a' }}>
              Workflow Completed
            </Text>
            {card.webhook_result && (
              <Tag color={card.webhook_result.ok ? 'green' : 'red'}>
                {card.webhook_result.ok
                  ? `Webhook \u2713 ${card.webhook_result.status}`
                  : `Webhook \u2717 ${card.webhook_result.error || card.webhook_result.status}`}
              </Tag>
            )}
          </Space>
          {card.prompt && (
            <div style={{ marginTop: 8, color: '#333', fontSize: 13 }}>{card.prompt}</div>
          )}
          {card.collected_data && Object.keys(card.collected_data).length > 0 && (
            <div
              style={{
                marginTop: 10,
                padding: '8px 12px',
                background: '#fff',
                borderRadius: 6,
                border: '1px solid #d9f7be',
              }}
            >
              <Text type="secondary" style={{ fontSize: 11, display: 'block', marginBottom: 6 }}>
                Collected Data
              </Text>
              {Object.entries(card.collected_data).map(([key, val]) => (
                <div key={key} style={{ fontSize: 12, marginBottom: 2 }}>
                  <Text strong style={{ fontSize: 12 }}>{key}: </Text>
                  <Text style={{ fontSize: 12 }}>{String(val)}</Text>
                </div>
              ))}
            </div>
          )}
        </div>
      );
    }

    /* ── Confirm step ── */
    if (isConfirm) {
      return (
        <div
          style={{
            marginTop: 8,
            padding: '12px 16px',
            background: '#fffbe6',
            border: '1px solid #ffe58f',
            borderRadius: 8,
          }}
        >
          <div style={{ marginBottom: 8 }}>
            <Tag color="orange">
              Step {card.current_step}/{card.total_steps}
            </Tag>
            <Text strong>{card.step_name}</Text>
          </div>
          <div style={{ marginBottom: 12, color: '#333', fontSize: 13 }}>
            {card.prompt || 'Please confirm the information above.'}
          </div>
          {isActive && (
            <Space>
              <Button type="primary" onClick={handleWorkflowConfirm}>
                Confirm
              </Button>
              <Button danger onClick={handleWorkflowCancel}>
                Cancel
              </Button>
            </Space>
          )}
        </div>
      );
    }

    /* ── Collect step (with fields) ── */
    if (!card.fields || card.fields.length === 0) return null;

    return (
      <div
        style={{
          marginTop: 8,
          padding: '12px 16px',
          background: '#f6ffed',
          border: '1px solid #b7eb8f',
          borderRadius: 8,
          maxWidth: '100%',
        }}
      >
        <div style={{ marginBottom: 8 }}>
          <Tag color="green">
            Step {card.current_step}/{card.total_steps}
          </Tag>
          <Text strong>{card.step_name}</Text>
        </div>
        {card.fields.map((field) => (
          <div key={field.name} style={{ marginBottom: 10 }}>
            <div style={{ marginBottom: 4 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {field.label}
                {field.required && <span style={{ color: 'red' }}> *</span>}
              </Text>
            </div>
            {isActive ? (
              /* ── Active: editable form controls ── */
              field.field_type === 'number' ? (
                <InputNumber
                  style={{ width: '100%' }}
                  placeholder={field.placeholder || `Enter ${field.label}`}
                  value={workflowFormData[field.name]}
                  onChange={(val) =>
                    setWorkflowFormData((prev) => ({ ...prev, [field.name]: val }))
                  }
                />
              ) : field.field_type === 'select' ? (
                <Select
                  style={{ width: '100%' }}
                  placeholder={field.placeholder || `Select ${field.label}`}
                  value={workflowFormData[field.name]}
                  onChange={(val) =>
                    setWorkflowFormData((prev) => ({ ...prev, [field.name]: val }))
                  }
                  options={field.options}
                />
              ) : field.field_type === 'file' ? (
                <div>
                  <Upload
                    beforeUpload={() => false}
                    maxCount={1}
                    accept={field.file_config?.allowed_extensions
                      ?.map((ext: string) => (ext.startsWith('.') ? ext : `.${ext}`))
                      .join(',')}
                    onChange={(info) =>
                      setWorkflowFormData((prev) => ({
                        ...prev,
                        [field.name]: info.fileList[0]?.originFileObj?.name || info.fileList[0]?.name,
                      }))
                    }
                  >
                    <Button icon={<UploadOutlined />} size="small">
                      Upload
                      {field.file_config?.max_size_mb && (
                        <span style={{ fontSize: 11, color: '#999' }}>
                          {' '}
                          (max {field.file_config.max_size_mb}MB)
                        </span>
                      )}
                    </Button>
                  </Upload>
                  {field.file_config?.allowed_extensions && (
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      Formats: {field.file_config.allowed_extensions.join(', ')}
                    </Text>
                  )}
                </div>
              ) : (
                <Input
                  placeholder={field.placeholder || `Enter ${field.label}`}
                  value={workflowFormData[field.name] ?? ''}
                  onChange={(e) =>
                    setWorkflowFormData((prev) => ({ ...prev, [field.name]: e.target.value }))
                  }
                />
              )
            ) : (
              /* ── Inactive: read-only display ── */
              <div style={{ fontSize: 12, color: '#999', fontStyle: 'italic' }}>
                {field.placeholder || field.label}
              </div>
            )}
          </div>
        ))}
        {isActive && (
          <Button
            type="primary"
            style={{ marginTop: 4 }}
            onClick={() => handleWorkflowSubmit(idx)}
            loading={sending}
          >
            Submit
          </Button>
        )}
      </div>
    );
  };

  /* ── Render ────────────────────────────────────────── */

  const selectedAgent = agents.find((a) => a.id === selectedAgentId);

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 136px)', gap: 0 }}>
      {/* ── Main Chat Area ────────────────────────────── */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          minWidth: 0,
        }}
      >
        {/* Top Bar */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 0 12px 0',
            borderBottom: '1px solid #f0f0f0',
            marginBottom: 12,
            flexWrap: 'wrap',
            gap: 8,
          }}
        >
          <Space wrap>
            <Select
              placeholder="Select Agent"
              value={selectedAgentId}
              onChange={(v) => {
                setSelectedAgentId(v);
                handleNewSession();
              }}
              loading={agentsLoading}
              style={{ minWidth: 240 }}
              optionLabelProp="label"
              showSearch
              filterOption={(input, option) =>
                (option?.label as string)?.toLowerCase().includes(input.toLowerCase()) ?? false
              }
            >
              {agents.map((a) => (
                <Select.Option key={a.id} value={a.id} label={a.name}>
                  <Space>
                    <RobotOutlined />
                    <span>{a.name}</span>
                    {a.enabled === false && <Tag color="red">Disabled</Tag>}
                  </Space>
                </Select.Option>
              ))}
            </Select>
            {selectedAgent && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {selectedAgent.description || 'No description'}
              </Text>
            )}
          </Space>
          <Space>
            <Tooltip title={useStreaming ? 'Streaming enabled' : 'Sync mode'}>
              <Button
                size="small"
                type={useStreaming ? 'primary' : 'default'}
                onClick={() => setUseStreaming(!useStreaming)}
              >
                {useStreaming ? 'SSE' : 'Sync'}
              </Button>
            </Tooltip>
            <Button icon={<ClearOutlined />} onClick={handleNewSession}>
              New Session
            </Button>
            {sessionId && (
              <Text copyable={{ text: sessionId }} type="secondary" style={{ fontSize: 11 }}>
                Session: {sessionId.slice(0, 8)}...
              </Text>
            )}
            <Button
              icon={tracePanelOpen ? <MenuFoldOutlined /> : <MenuUnfoldOutlined />}
              onClick={() => setTracePanelOpen(!tracePanelOpen)}
              title={tracePanelOpen ? 'Hide trace panel' : 'Show trace panel'}
            />
          </Space>
        </div>

        {/* Chat Messages */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '8px 0',
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          {messages.length === 0 && (
            <Empty
              description={
                selectedAgentId
                  ? 'Send a message to start chatting'
                  : 'Select an agent to begin'
              }
              style={{ margin: 'auto' }}
            />
          )}

          {messages.map((msg, idx) => (
            <div
              key={idx}
              style={{
                display: 'flex',
                justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                padding: '0 8px',
              }}
            >
              <div
                style={{
                  maxWidth: '75%',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
                }}
              >
                {/* Avatar + role label */}
                <Space style={{ marginBottom: 4 }}>
                  {msg.role === 'user' ? (
                    <Tag icon={<UserOutlined />} color="blue">
                      You
                    </Tag>
                  ) : (
                    <Tag icon={<RobotOutlined />} color="green">
                      Assistant
                    </Tag>
                  )}
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {new Date(msg.timestamp).toLocaleTimeString()}
                  </Text>
                </Space>

                {/* Message bubble */}
                <div
                  style={{
                    padding: '10px 14px',
                    borderRadius: msg.role === 'user' ? '12px 12px 2px 12px' : '12px 12px 12px 2px',
                    background: msg.error
                      ? '#fff2f0'
                      : msg.role === 'user'
                      ? '#1677ff'
                      : '#f5f5f5',
                    color: msg.error ? '#ff4d4f' : msg.role === 'user' ? '#fff' : '#333',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    lineHeight: 1.6,
                    fontSize: 14,
                    border: msg.error ? '1px solid #ffccc7' : 'none',
                  }}
                >
                  {msg.isStreaming && !msg.content ? (
                    <Spin size="small" />
                  ) : (
                    msg.content
                  )}
                  {msg.isStreaming && msg.content && (
                    <Spin size="small" style={{ marginLeft: 8 }} />
                  )}
                </div>

                {/* Skill info badge */}
                {msg.skillInfo && (
                  <div style={{ marginTop: 4 }}>
                    <Tag color="purple" style={{ fontSize: 11 }}>
                      {msg.skillInfo.skill_name || msg.skillInfo.skill_type}
                    </Tag>
                    {msg.skillInfo.delegated_to && (
                      <Tag color="gold" style={{ fontSize: 11 }}>
                        delegated
                      </Tag>
                    )}
                  </div>
                )}

                {/* Workflow Card (interactive form / confirm / completed) */}
                {renderWorkflowCard(msg, idx)}

                {/* Citations */}
                {msg.citations && msg.citations.length > 0 && (
                  <div style={{ marginTop: 6 }}>
                    {msg.citations.map((c, ci) => (
                      <Tag
                        key={ci}
                        color="geekblue"
                        style={{ cursor: 'pointer', marginBottom: 2 }}
                        onClick={() => setActiveCitations(msg.citations!)}
                      >
                        [{ci + 1}] {c.source_name}
                        {c.score != null && ` (${(c.score * 100).toFixed(0)}%)`}
                      </Tag>
                    ))}
                  </div>
                )}

                {/* Followup suggestions */}
                {msg.followups && msg.followups.length > 0 && (
                  <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {msg.followups.map((q, qi) => (
                      <Tag
                        key={qi}
                        color="default"
                        style={{ cursor: 'pointer', borderStyle: 'dashed' }}
                        onClick={() => handleFollowup(q)}
                      >
                        {q}
                      </Tag>
                    ))}
                  </div>
                )}

                {/* Trace link */}
                {msg.traceId && (
                  <Button
                    type="link"
                    size="small"
                    style={{ padding: 0, marginTop: 4, fontSize: 11 }}
                    onClick={() => loadTrace(msg.traceId!)}
                  >
                    View Trace: {msg.traceId.slice(0, 8)}...
                  </Button>
                )}
              </div>
            </div>
          ))}

          <div ref={chatEndRef} />
        </div>

        {/* Input Area */}
        <div
          style={{
            borderTop: '1px solid #f0f0f0',
            paddingTop: 12,
            display: 'flex',
            gap: 8,
            alignItems: 'flex-end',
          }}
        >
          <Tooltip title="Voice Input">
            <Button
              icon={<AudioOutlined />}
              loading={transcribing}
              disabled={sending || transcribing}
              onClick={() => setVoicePanelOpen(true)}
            />
          </Tooltip>
          <TextArea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              selectedAgentId ? 'Type a message... (Enter to send, Shift+Enter for new line)' : 'Select an agent first'
            }
            disabled={!selectedAgentId || sending}
            autoSize={{ minRows: 1, maxRows: 4 }}
            style={{ flex: 1 }}
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={handleSend}
            loading={sending}
            disabled={!selectedAgentId || !inputValue.trim()}
          >
            Send
          </Button>
        </div>

        <Modal
          title="Voice Input"
          open={voicePanelOpen}
          onCancel={() => {
            if (recording) stopRecording();
            setVoicePanelOpen(false);
          }}
          footer={null}
          width={520}
        >
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Upload.Dragger
              accept="audio/*,.wav,.mp3,.m4a,.aac,.ogg,.opus,.flac,.webm"
              beforeUpload={handleAudioUpload}
              showUploadList={false}
              disabled={transcribing || recording}
              multiple={false}
              style={{ padding: 12 }}
            >
              <p className="ant-upload-drag-icon">
                <UploadOutlined />
              </p>
              <p className="ant-upload-text">Upload Audio</p>
              <p className="ant-upload-hint">WAV, MP3, M4A, AAC, OGG, OPUS, FLAC, WEBM</p>
            </Upload.Dragger>

            <div
              style={{
                border: '1px solid #f0f0f0',
                borderRadius: 8,
                padding: 16,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 12,
              }}
            >
              <Space>
                <AudioOutlined />
                <Text strong>Record Audio</Text>
                {recording && <Tag color="red">{formatRecordingTime(recordingSeconds)}</Tag>}
              </Space>
              <Button
                type={recording ? 'default' : 'primary'}
                danger={recording}
                icon={<AudioOutlined />}
                onClick={recording ? stopRecording : startRecording}
                disabled={transcribing}
              >
                {recording ? 'Stop & Transcribe' : 'Start Recording'}
              </Button>
            </div>

            {transcribing && (
              <div style={{ textAlign: 'center', padding: '8px 0' }}>
                <Spin size="small" /> <Text type="secondary">Transcribing...</Text>
              </div>
            )}
          </Space>
        </Modal>
      </div>

      {/* ── Trace / Citations Panel (Collapsible) ─────── */}
      {tracePanelOpen && (
        <div
          style={{
            width: 340,
            minWidth: 280,
            borderLeft: '1px solid #f0f0f0',
            paddingLeft: 16,
            marginLeft: 16,
            overflowY: 'auto',
            flexShrink: 0,
          }}
        >
          <Collapse
            defaultActiveKey={['citations', 'trace']}
            ghost
            items={[
              {
                key: 'citations',
                label: (
                  <Space>
                    <Text strong>Citations</Text>
                    {activeCitations.length > 0 && (
                      <Tag color="blue">{activeCitations.length}</Tag>
                    )}
                  </Space>
                ),
                children:
                  activeCitations.length === 0 ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      No citations yet. Send a message to see retrieval results.
                    </Text>
                  ) : (
                    <List
                      size="small"
                      dataSource={activeCitations}
                      renderItem={(c, i) => (
                        <List.Item style={{ padding: '6px 0' }}>
                          <div style={{ width: '100%' }}>
                            <Space style={{ marginBottom: 4 }}>
                              <Tag color="geekblue">[{i + 1}]</Tag>
                              <Text strong style={{ fontSize: 12 }}>
                                {c.source_name}
                              </Text>
                              {c.score != null && (
                                <Tag color={c.score > 0.8 ? 'green' : c.score > 0.5 ? 'orange' : 'red'}>
                                  {(c.score * 100).toFixed(0)}%
                                </Tag>
                              )}
                            </Space>
                            <Paragraph
                              ellipsis={{ rows: 3, expandable: true, symbol: 'more' }}
                              style={{
                                fontSize: 12,
                                background: '#f5f5f5',
                                padding: '6px 8px',
                                borderRadius: 4,
                                margin: 0,
                              }}
                            >
                              {c.content_snippet}
                            </Paragraph>
                            {(c.page != null || c.line_start != null) && (
                              <Text type="secondary" style={{ fontSize: 11 }}>
                                {c.page != null && `Page ${c.page}`}
                                {c.page != null && c.line_start != null && ' | '}
                                {c.line_start != null && `Lines ${c.line_start}-${c.line_end ?? '?'}`}
                              </Text>
                            )}
                          </div>
                        </List.Item>
                      )}
                    />
                  ),
              },
              {
                key: 'trace',
                label: (
                  <Space>
                    <Text strong>Call Trace</Text>
                    {traceEvents.length > 0 && (
                      <Tag color="purple">{traceEvents.length}</Tag>
                    )}
                    {traceLoading && <Spin size="small" />}
                  </Space>
                ),
                children:
                  traceEvents.length === 0 ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      No trace data. Trace will appear after a response is received.
                    </Text>
                  ) : (
                    <Timeline
                      items={traceEvents.map((t) => ({
                        color: EVENT_COLORS[t.event_type] || 'gray',
                        children: (
                          <div style={{ marginBottom: 8 }}>
                            <Space style={{ marginBottom: 4 }}>
                              <Tag color={EVENT_COLORS[t.event_type] || 'default'} style={{ fontSize: 11 }}>
                                {t.event_type}
                              </Tag>
                              {t.latency_ms != null && (
                                <Text type="secondary" style={{ fontSize: 11 }}>
                                  {t.latency_ms.toFixed(1)}ms
                                </Text>
                              )}
                            </Space>
                            {t.event_data && (
                              <pre
                                style={{
                                  fontSize: 11,
                                  maxHeight: 120,
                                  overflow: 'auto',
                                  background: '#f5f5f5',
                                  padding: 6,
                                  borderRadius: 4,
                                  margin: '4px 0 0 0',
                                  whiteSpace: 'pre-wrap',
                                  wordBreak: 'break-all',
                                }}
                              >
                                {JSON.stringify(t.event_data, null, 2)}
                              </pre>
                            )}
                            {t.retrieval_hits && (
                              <pre
                                style={{
                                  fontSize: 11,
                                  maxHeight: 120,
                                  overflow: 'auto',
                                  background: '#f0f5ff',
                                  padding: 6,
                                  borderRadius: 4,
                                  margin: '4px 0 0 0',
                                  whiteSpace: 'pre-wrap',
                                  wordBreak: 'break-all',
                                }}
                              >
                                {JSON.stringify(t.retrieval_hits, null, 2)}
                              </pre>
                            )}
                            {t.llm_meta && (
                              <pre
                                style={{
                                  fontSize: 11,
                                  maxHeight: 120,
                                  overflow: 'auto',
                                  background: '#f9f0ff',
                                  padding: 6,
                                  borderRadius: 4,
                                  margin: '4px 0 0 0',
                                  whiteSpace: 'pre-wrap',
                                  wordBreak: 'break-all',
                                }}
                              >
                                {JSON.stringify(t.llm_meta, null, 2)}
                              </pre>
                            )}
                            {t.tool_meta && (
                              <pre
                                style={{
                                  fontSize: 11,
                                  maxHeight: 120,
                                  overflow: 'auto',
                                  background: '#fff7e6',
                                  padding: 6,
                                  borderRadius: 4,
                                  margin: '4px 0 0 0',
                                  whiteSpace: 'pre-wrap',
                                  wordBreak: 'break-all',
                                }}
                              >
                                {JSON.stringify(t.tool_meta, null, 2)}
                              </pre>
                            )}
                          </div>
                        ),
                      }))}
                    />
                  ),
              },
            ]}
          />
        </div>
      )}
    </div>
  );
}
