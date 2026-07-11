// Conversation session / message types — mirrors server/api/sessions.py
// response shapes (server/models/session.py: ConversationSession, Message).

export interface ConversationSessionSummary {
  id: string;
  agent_id: string;
  user_id: string;
  status: string;
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
  citations: Record<string, unknown> | null;
  suggested_followups: Record<string, unknown> | null;
  created_at: string | null;
}

export interface ConversationMessageListResponse {
  total: number;
  offset: number;
  limit: number;
  items: ConversationMessage[];
}
