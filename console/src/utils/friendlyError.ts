import type { TFunction } from 'i18next';
import { ApiError } from '../api';

/**
 * Turn an unknown error (typically an ApiError thrown by the centralized
 * api.ts response interceptor, but also raw fetch()/network failures) into
 * a human-readable, actionable message for non-technical users.
 *
 * Priority: HTTP status on ApiError > known message patterns (timeout /
 * network) > generic fallback that still surfaces the original text so
 * nothing is silently swallowed.
 */
export function friendlyError(e: unknown, t: TFunction): string {
  if (e instanceof ApiError && typeof e.status === 'number') {
    if (e.status === 401) return t('errors.unauthorized');
    if (e.status === 403) return t('errors.forbidden');
    if (e.status === 429) return t('errors.rateLimited');
    if (e.status >= 500) {
      const detail = e.detail || e.message;
      return `${t('errors.serverError')}${detail ? ` (${detail})` : ''}`;
    }
  }

  const message = e instanceof Error ? e.message : String(e);

  if (/timeout|ECONNABORTED/i.test(message)) return t('errors.timeout');
  if (/network error/i.test(message)) return t('errors.network');

  return `${t('errors.generic')}${message ? ` (${message})` : ''}`;
}
