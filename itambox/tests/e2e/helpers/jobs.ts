import { expect, type APIRequestContext } from '@playwright/test';
import { objectRows } from './api';

export type JobRecord = Record<string, unknown>;

export async function waitForJob(
  request: APIRequestContext,
  path: string,
  terminalStates: readonly string[] = ['completed', 'failed', 'cancelled'],
): Promise<JobRecord> {
  let last: JobRecord | undefined;
  const state = await expect.poll(
    async () => {
      const response = await request.get(path);
      const body = await response.text();
      expect(response.status(), `GET ${path}: ${body}`).toBe(200);
      let parsed: unknown;
      try {
        parsed = JSON.parse(body);
      } catch (error) {
        throw new Error(`GET ${path}: expected JSON (${String(error)}).`);
      }
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error(`GET ${path}: expected a job object.`);
      }
      last = parsed as JobRecord;
      return String(last.status || last.state || '');
    },
    { timeout: 30_000, intervals: [100, 250, 500, 1_000] },
  );
  expect(terminalStates, `Job ${path} reached unexpected state ${state}`).toContain(state);
  if (!last) throw new Error(`GET ${path}: job did not produce a record.`);
  return last;
}

export function assertJobRows(value: unknown, label: string): JobRecord[] {
  return objectRows(value, label) as JobRecord[];
}
