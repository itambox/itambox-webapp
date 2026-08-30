import type { TestInfo } from '@playwright/test';

function slug(value: string): string {
  const result = value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48);
  return result || 'test';
}

/**
 * Return an identity that cannot be reused by a retry of the same test.
 *
 * CI supplies GITHUB_RUN_ID. Local runs may provide E2E_RUN_ID; the process
 * fallback keeps two local invocations distinct without exposing test data.
 */
export function runIdentity(testInfo: TestInfo): string {
  const configured = process.env.E2E_RUN_ID || process.env.GITHUB_RUN_ID;
  if (configured) return slug(configured);
  return `local-${process.pid}-${testInfo.startTime.getTime()}`;
}

export function retrySafeName(testInfo: TestInfo, suffix: string): string {
  return [
    'e2e',
    slug(testInfo.project.name),
    `w${testInfo.workerIndex}`,
    `r${testInfo.retry}`,
    runIdentity(testInfo),
    slug(suffix),
  ].join('-');
}

export function uniqueName(testInfo: TestInfo, suffix: string): string {
  return retrySafeName(testInfo, suffix);
}
