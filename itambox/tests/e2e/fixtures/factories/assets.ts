import { expect, type APIRequestContext } from '@playwright/test';
import type { CleanupRegistry } from '../cleanup';
import { getJsonRows, jsonResponse, type JsonObject } from '../../helpers/api';

export type OwnedAsset = {
  id: string;
  assetTag: string;
  tenant: string;
};

function primaryKey(row: JsonObject, label: string): string {
  const value = row.id;
  if (typeof value !== 'string' && typeof value !== 'number') {
    throw new Error(`${label} has no usable ID.`);
  }
  return String(value);
}

export async function createOwnedAsset(
  request: APIRequestContext,
  cleanup: CleanupRegistry,
  tenant: string,
  runId: string,
): Promise<OwnedAsset> {
  const assetTypes = await getJsonRows(request, '/api/assets/asset-types/?limit=100', 'asset type prerequisites');
  expect(assetTypes, 'the seeded E2E database must provide an asset type').not.toHaveLength(0);
  const assetTypeId = primaryKey(assetTypes[0], 'asset type');
  const assetTag = `E2E-${runId}`.slice(0, 90);
  const response = await request.post('/api/assets/assets/', {
    data: {
      name: `E2E owned asset ${runId}`,
      asset_tag: assetTag,
      asset_type_id: assetTypeId,
      tenant_id: tenant,
    },
  });
  const body = await jsonResponse(response, 201, 'create owned asset');
  const id = primaryKey(body, 'created asset');
  expect(body.asset_tag, 'created asset must preserve its owned tag').toBe(assetTag);
  const owned = { id, assetTag, tenant };
  cleanup.add(`asset ${assetTag}`, async () => {
    const deletion = await request.delete(`/api/assets/assets/${id}/`);
    const text = await deletion.text();
    expect(deletion.status(), `delete owned asset ${assetTag}: ${text}`).toBe(204);
  });
  return owned;
}
