/** Turns a raw LLM test-connection error string into a translation key. */
export type ConnectionErrorKind = 'authError' | 'networkError' | 'otherError';

const AUTH_PATTERN = /\b401\b|\b403\b|unauthorized|forbidden|invalid api key|invalid_api_key/i;
const NETWORK_PATTERN = /timeout|timed out|connect|refused|resolve|unreachable|network/i;

export function classifyConnectionError(rawMessage: string): ConnectionErrorKind {
  if (AUTH_PATTERN.test(rawMessage)) return 'authError';
  if (NETWORK_PATTERN.test(rawMessage)) return 'networkError';
  return 'otherError';
}
