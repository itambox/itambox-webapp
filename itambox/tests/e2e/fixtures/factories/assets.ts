import { expect, type APIRequestContext } from '@playwright/test';
import type { CleanupRegistry } from '../cleanup';
import { deleteOwnedResource, getJsonRows, jsonResponse, type JsonObject } from '../../helpers/api';

export type OwnedAsset = {
  id: string;
  assetTag: string;
  name: string;
  tenant: string;
};

function primaryKey(row: JsonObject, label: string): string {
  const value = row.id;
  if (typeof value !== 'string' && typeof value !== 'number') {
    throw new Error(`${label} has no usable ID.`);
  }
  return String(value);
}

export type OwnedAssetCleanupOptions = {
  preserveProtectedHistory?: boolean;
};

export async function createOwnedAsset(
  request: APIRequestContext,
  cleanup: CleanupRegistry,
  tenant: string,
  runId: string,
  options: OwnedAssetCleanupOptions = {},
): Promise<OwnedAsset> {
  const assetTypes = await getJsonRows(request, '/api/assets/asset-types/?limit=100', 'asset type prerequisites');
  expect(assetTypes, 'the seeded E2E database must provide an asset type').not.toHaveLength(0);
  const assetTypeId = primaryKey(assetTypes[0], 'asset type');
  const assetTag = `E2E-${runId}`.slice(0, 90);
  const name = `E2E owned asset ${runId}`;
  const response = await request.post('/api/assets/assets/', {
    data: {
      name,
      asset_tag: assetTag,
      asset_type_id: assetTypeId,
      tenant_id: tenant,
    },
  });
  const body = await jsonResponse(response, 201, 'create owned asset');
  const id = primaryKey(body, 'created asset');
  expect(body.asset_tag, 'created asset must preserve its owned tag').toBe(assetTag);
  const owned = { id, assetTag, name, tenant };
  cleanup.add(`asset ${assetTag}`, async () => {
    if (options.preserveProtectedHistory) {
      const current = await request.get(`/api/assets/assets/${id}/`);
      expect(current.status(), `preserved asset ${assetTag} cleanup readback`).toBe(200);
      return;
    }
    await deleteOwnedResource(request, `/api/assets/assets/${id}/`, `delete owned asset ${assetTag}`);
  });
  return owned;
}
