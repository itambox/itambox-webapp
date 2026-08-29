import { expect, type APIRequestContext } from '@playwright/test';
import type { CleanupRegistry } from '../cleanup';
import { jsonResponse } from '../../helpers/api';

export type OwnedAssetHolder = {
  id: string;
  upn: string;
  tenant: string;
};

export async function createOwnedAssetHolder(
  request: APIRequestContext,
  cleanup: CleanupRegistry,
  tenant: string,
  upn: string,
): Promise<OwnedAssetHolder> {
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
    const deletion = await request.delete(`/api/organization/asset-holders/${owned.id}/`);
    const text = await deletion.text();
    expect([204, 404], `delete owned asset holder ${upn}: ${text}`).toContain(deletion.status());
  });
  return owned;
}
