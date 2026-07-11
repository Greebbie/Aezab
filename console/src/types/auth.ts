export interface AuthStatus {
  auth_disabled: boolean;
  needs_setup: boolean;
}

export interface AuthUser {
  id: string;
  username: string;
  role: string;
  tenant_id: string;
  display_name: string;
  enabled: boolean;
  created_at?: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in_minutes: number;
  user: AuthUser;
}
