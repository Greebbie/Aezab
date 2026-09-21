// Conversation session / message types — mirrors server/api/sessions.py
// response shapes (server/models/session.py: ConversationSession, Message).
import type { Citation, WorkflowCard } from './invoke';

export interface ConversationSessionSummary {
  id: string;
  agent_id: string;
  user_id: string;
  status: string;
  workflow_status: string | null;
  workflow_id: string | null;
  workflow_version: number | null;
  message_count: number;
  created_at: string | null;
  updated_at: string | null;
  title: string | null;
}

export interface ConversationSessionListResponse {
  total: number;
  offset: number;
  limit: number;
  items: ConversationSessionSummary[];
}

export interface ConversationMessage {
  id: string;
  role: string;
  content: string;
  short_answer: string | null;
  expanded_answer: string | null;
  citations: Citation[] | null;
  suggested_followups: string[] | null;
  trace_id: string | null;
  workflow_card: WorkflowCard | null;
  workflow_status: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
}

export interface ConversationMessageListResponse {
  total: number;
  offset: number;
  limit: number;
  items: ConversationMessage[];
}
