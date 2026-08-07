import createClient from "openapi-fetch";

import type { paths } from "./schema.js";

declare const process: {
  env: Record<string, string | undefined>;
  exitCode?: number;
};

const baseUrl = process.env.OPENAPI_CLIENT_BASE_URL ?? "http://127.0.0.1:8000";

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} must be set for the OpenAPI client smoke test`);
  }
  return value;
}

const token = requiredEnv("E2E_SCIM_TOKEN");
const tenantSlug = requiredEnv("E2E_TENANT_SLUG");

function assertStatus(actual: number, expected: number, operation: string): void {
  if (actual !== expected) {
    throw new Error(`${operation}: expected HTTP ${expected}, received ${actual}`);
  }
}

async function main(): Promise<void> {
  const unauthenticated = createClient<paths>({ baseUrl });
  const unauthenticatedList = await unauthenticated.GET("/api/extras/tags/");
  assertStatus(unauthenticatedList.response.status, 401, "authentication");

  const client = createClient<paths>({
    baseUrl,
    headers: { Authorization: `Token ${token}` },
  });
  const scimClient = createClient<paths>({
    baseUrl,
    headers: { Authorization: `Bearer ${token}` },
  });

  const scimUsers = await scimClient.GET("/api/tenants/{tenant_slug}/scim/v2/Users", {
    params: {
      path: { tenant_slug: tenantSlug },
      query: { startIndex: 1, count: 1 },
    },
  });
  assertStatus(scimUsers.response.status, 200, "SCIM bearer authentication and pagination");
  if (scimUsers.data?.totalResults === undefined) {
    throw new Error("SCIM users response did not contain totalResults");
  }

  const paginatedAssets = await client.GET("/api/assets/assets/", {
    params: { query: { limit: 1 } },
  });
  assertStatus(paginatedAssets.response.status, 200, "REST pagination");
  if (paginatedAssets.data?.count === undefined) {
    throw new Error("paginated asset response did not contain count");
  }

  const invalidTag = await client.POST("/api/extras/tags/", {
    body: { name: "OpenAPI invalid", slug: "not a valid slug" },
  });
  assertStatus(invalidTag.response.status, 400, "validation failure");

  const slug = `openapi-smoke-${Date.now()}`;
  let tagId: number | undefined;
  let etag: string | null = null;
  try {
    const created = await client.POST("/api/extras/tags/", {
      body: { name: "OpenAPI smoke", slug },
    });
    assertStatus(created.response.status, 201, "create");
    if (!created.data?.id) {
      throw new Error("create response did not contain a tag id");
    }
    tagId = created.data.id;
    etag = created.response.headers.get("ETag");
    if (!etag) {
      throw new Error("create response did not contain an ETag");
    }

    const updated = await client.PATCH("/api/extras/tags/{id}/", {
      params: { path: { id: tagId } },
      headers: { "If-Match": etag },
      body: { name: "OpenAPI smoke updated", slug },
    });
    assertStatus(updated.response.status, 200, "update");
    etag = updated.response.headers.get("ETag");
    if (!etag) {
      throw new Error("update response did not contain an ETag");
    }

    const retrieved = await client.GET("/api/extras/tags/{id}/", {
      params: { path: { id: tagId } },
    });
    assertStatus(retrieved.response.status, 200, "read");
    etag = retrieved.response.headers.get("ETag") ?? etag;

    const deleted = await client.DELETE("/api/extras/tags/{id}/", {
      params: { path: { id: tagId } },
      headers: { "If-Match": etag },
    });
    assertStatus(deleted.response.status, 204, "delete");
    tagId = undefined;
  } finally {
    if (tagId !== undefined) {
      await client.DELETE("/api/extras/tags/{id}/", {
        params: { path: { id: tagId } },
        headers: { "If-Match": etag ?? "*" },
      });
    }
  }
}

main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
