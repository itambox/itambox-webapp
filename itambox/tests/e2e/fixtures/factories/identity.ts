import { expect, type APIRequestContext } from '@playwright/test';
import type { CleanupRegistry } from '../cleanup';
import { deleteOwnedResource, jsonResponse } from '../../helpers/api';

export type OwnedAssetHolder = {
  id: string;
  upn: string;
  tenant: string;
};

export async function createOwnedAssetHolder(
  request: APIRequestContext,
  cleanup: CleanupRegistry,
  tenant: string,
  semantic: string,
): Promise<OwnedAssetHolder> {
  const localPart = semantic
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48);
  const upn = `${localPart || 'e2e-holder'}@example.invalid`;
  const payload = {
    tenant_id: tenant,
    first_name: 'E2E',
    last_name: 'Holder',
    upn,
    email: upn,
  };
  const response = await request.post('/api/organization/asset-holders/', { data: payload });
  const body = await jsonResponse(response, 201, 'create owned asset holder');
  const rawId = body.id;
  if (typeof rawId !== 'string' && typeof rawId !== 'number') {
    throw new Error('Created AssetHolder response has no ID.');
  }
  const owned: OwnedAssetHolder = { id: String(rawId), upn, tenant };
  cleanup.add(`asset holder ${upn}`, async () => {
    await deleteOwnedResource(
      request,
      `/api/organization/asset-holders/${owned.id}/`,
      `delete owned asset holder ${upn}`,
    );
  });
  return owned;
}
