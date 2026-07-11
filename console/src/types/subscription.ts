export interface EventSubscription {
  id: string;
  tenant_id: string;
  name: string;
  url: string;
  events: string[];
  enabled: boolean;
  created_at: string | null;
}

export interface EventSubscriptionCreate {
  name: string;
  url: string;
  secret: string;
  events: string[];
  enabled?: boolean;
}

// secret is intentionally omitted here on update when left blank by the
// caller — the server keeps the existing secret unless a new one is sent.
export interface EventSubscriptionUpdate {
  name?: string;
  url?: string;
  secret?: string;
  events?: string[];
  enabled?: boolean;
}
