import { expect, type APIRequestContext, type Page } from '@playwright/test';

export type JsonObject = Record<string, unknown>;

export async function jsonResponse(
  response: Awaited<ReturnType<APIRequestContext['get']>>,
  expectedStatus: number,
  label: string,
): Promise<JsonObject> {
  const body = await response.text();
  expect(response.status(), `${label}: ${body}`).toBe(expectedStatus);
  let value: unknown;
  try {
    value = JSON.parse(body);
  } catch (error) {
    throw new Error(`${label}: expected JSON response (${String(error)}).`);
  }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label}: expected a JSON object.`);
  }
  return value as JsonObject;
}

export function objectRows(value: unknown, label: string): JsonObject[] {
  const rows = Array.isArray(value)
    ? value
    : value !== null && typeof value === 'object' && !Array.isArray(value)
      ? (value as JsonObject).results
      : undefined;
  if (!Array.isArray(rows) || !rows.every((row) => row !== null && typeof row === 'object' && !Array.isArray(row))) {
    throw new Error(`${label}: expected an array or paginated object containing object rows.`);
  }
  return rows as JsonObject[];
}

export async function getJsonRows(
  request: APIRequestContext,
  path: string,
  label = `GET ${path}`,
): Promise<JsonObject[]> {
  const response = await request.get(path);
  const body = await response.text();
  expect(response.status(), `${label}: ${body}`).toBe(200);
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch (error) {
    throw new Error(`${label}: expected JSON response (${String(error)}).`);
  }
  return objectRows(parsed, label);
}

export async function assertPageResponse(page: Page, expectedStatus = 200): Promise<void> {
  const response = await page.waitForEvent('response', {
    predicate: (candidate) => candidate.url() === page.url() && candidate.request().method() === 'GET',
  });
  expect(response.status(), `GET ${page.url()}`).toBe(expectedStatus);
}
