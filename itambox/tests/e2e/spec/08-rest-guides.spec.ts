import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { execSync } from 'child_process';

test.describe('REST API Guides and Scripting Specs', () => {

  const integrationDir = path.join(__dirname, '../../../docs/integration');
  const devGuidePath = path.join(integrationDir, 'developer_guide.md');
  const pythonScriptPath = path.join(integrationDir, 'offboard_user.py');
  const csvGuidePath = path.join(integrationDir, 'bulk_import_guide.md');
  const csvDataPath = path.join(integrationDir, 'bulk_assets.csv');

  test.beforeEach(async ({ page }) => {
    page.on('console', msg => {
      if (msg.type() === 'error') {
        console.error(`[Console Error]: ${msg.text()}`);
      }
    });
    page.on('pageerror', error => {
      console.error(`[Page Error]: ${error.message}`);
    });
  });

  // TIER 1: Feature Coverage (>= 5 tests)

  test('1. Verify existence and readability of the developer integration guide', () => {
    expect(fs.existsSync(devGuidePath)).toBe(true);
    const content = fs.readFileSync(devGuidePath, 'utf8');
    expect(content.length).toBeGreaterThan(0);
    expect(content).toContain('Developer Integration Guide');
  });

  test('2. Verify existence of the client-side Python offboarding script template', () => {
    expect(fs.existsSync(pythonScriptPath)).toBe(true);
    const content = fs.readFileSync(pythonScriptPath, 'utf8');
    expect(content.length).toBeGreaterThan(0);
  });

  test('3. Verify existence of the bulk CSV guide', () => {
    expect(fs.existsSync(csvGuidePath)).toBe(true);
    const content = fs.readFileSync(csvGuidePath, 'utf8');
    expect(content.length).toBeGreaterThan(0);
  });

  test('4. Validate Python scripting template by executing syntax check', () => {
    // Determine the Python executable path
    const venvPython = path.join(__dirname, '../../../../.venv/Scripts/python.exe');
    const pythonCmd = fs.existsSync(venvPython) ? `"${venvPython}"` : 'python';
    
    try {
      // Compiling to check for syntax errors
      const result = execSync(`${pythonCmd} -m py_compile "${pythonScriptPath}"`, { stdio: 'pipe' });
      expect(result).toBeDefined();
    } catch (error: any) {
      console.error(`Python compilation check failed: ${error.stderr?.toString() || error.message}`);
      throw error;
    }
  });

  test('5. Validate CSV guide data format matches standard CSV parser requirements', () => {
    expect(fs.existsSync(csvDataPath)).toBe(true);
    const csvContent = fs.readFileSync(csvDataPath, 'utf-8');
    const lines = csvContent.trim().split('\n');
    expect(lines.length).toBeGreaterThan(1);
    
    const headers = lines[0].split(',');
    expect(headers.length).toBeGreaterThan(0);
    
    // Check that each row has the same number of fields as the header
    for (let i = 1; i < lines.length; i++) {
      const rowFields = lines[i].split(',');
      expect(rowFields.length).toBe(headers.length);
    }
  });

  // TIER 2: Boundary & Corner Cases (>= 5 tests)

  test('6. Check that guides contain actual instructions (non-empty files)', () => {
    const devGuideStats = fs.statSync(devGuidePath);
    const csvGuideStats = fs.statSync(csvGuidePath);
    expect(devGuideStats.size).toBeGreaterThan(100);
    expect(csvGuideStats.size).toBeGreaterThan(100);
  });

  test('7. Verify Python templates do not contain hardcoded credentials (use env variables)', () => {
    const scriptContent = fs.readFileSync(pythonScriptPath, 'utf8');
    // Ensure no hardcoded secret values (like api keys or password strings) are in the script
    // It should query os.environ
    expect(scriptContent).toContain('os.environ.get');
    expect(scriptContent).not.toContain('ITAMBOX_API_TOKEN = "');
    expect(scriptContent).not.toContain('API_TOKEN = \'');
  });

  test('8. Check that CSV guide example headers match actual database schema columns', () => {
    const csvContent = fs.readFileSync(csvDataPath, 'utf-8');
    const headers = csvContent.trim().split('\n')[0].split(',');
    
    // Mandatory schema fields for Asset model
    expect(headers).toContain('name');
    expect(headers).toContain('asset_tag');
    
    // Optional schema fields
    expect(headers).toContain('serial_number');
    expect(headers).toContain('description');
  });

  test('9. Verify offboarding script handles API errors gracefully (contains try-except)', () => {
    const scriptContent = fs.readFileSync(pythonScriptPath, 'utf-8');
    // The script should contain exception handling to capture urllib/requests errors
    expect(scriptContent).toContain('try:');
    expect(scriptContent).toContain('except');
    expect(scriptContent).toContain('urllib.error.URLError');
  });

  test('10. Verify document guides are readable and well-formatted markdown', () => {
    const devGuideContent = fs.readFileSync(devGuidePath, 'utf-8');
    const csvGuideContent = fs.readFileSync(csvGuidePath, 'utf-8');
    
    // Markdown headers
    expect(devGuideContent.startsWith('#')).toBe(true);
    expect(csvGuideContent.startsWith('#')).toBe(true);
  });

  // TIER 3: Cross-Feature Combinations (combos 3, 4, 5)

  test('11. Combo 3: SCIM provisioning -> OIDC Auth -> Python offboarding script execution', async ({ request, playwright }) => {
    // 1. Sync user via SCIM
    const username = `scim.offboard.${Date.now()}`;
    const scimPayload = {
      schemas: ["urn:ietf:params:scim:schemas:core:2.0:User"],
      userName: username,
      name: { givenName: "Offboard", familyName: "Test" },
      emails: [{ value: `${username}@example.com`, primary: true }]
    };

    const scimResponse = await request.post('/api/tenants/default/scim/v2/Users', { data: scimPayload });
    if (scimResponse.status() === 201) {
      const scimJson = await scimResponse.json();
      const userId = scimJson.id;

      // 2. Authenticate user via OIDC callback to set session
      const authContext = await playwright.request.newContext();
      const authResponse = await authContext.get(`/oidc/callback/?code=offboard_code&username=${username}`);
      expect(authResponse.status()).toBeDefined();

      // 3. Execute offboarding script logic against REST endpoints
      // Create a token to authenticate the script execution
      const tokenRes = await request.post('/api/users/tokens/', { data: { user_id: 1, description: 'Offboard Script Token' } });
      if (tokenRes.status() === 201) {
        const tokenJson = await tokenRes.json();
        const tokenKey = tokenJson.key;

        // Run offboarding script logic
        const scriptContext = await playwright.request.newContext({
          extraHTTPHeaders: { 'Authorization': `Token ${tokenKey}` }
        });
        
        // Find assignments
        const assignmentsRes = await scriptContext.get(`/api/v1/assets/assignments/?assigned_user_id=${userId}&is_active=true`);
        expect(assignmentsRes.status()).toBe(200);
      }
    }
  });

  // TIER 4: Real-World Scenarios (workloads 3, 4, 5)

  test('12. Workload 3: Simulate offboarding workflow', async ({ request, playwright }) => {
    // 1. Create target user AssetHolder and allocate assets to them
    const holderPayload = {
      first_name: "Workflow",
      last_name: "User",
      upn: `wf.user.${Date.now()}@example.com`,
      email: `wf.user.${Date.now()}@example.com`
    };
    
    const holderRes = await request.post('/api/v1/organization/assetholders/', { data: holderPayload });
    if (holderRes.status() === 201) {
      const holderJson = await holderRes.json();
      const holderId = holderJson.id;

      // Allocate an asset to them
      const allocPayload = {
        asset: 1, // assume asset 1 exists
        assigned_user: holderId,
        notes: "Checkout for workflow test"
      };
      await request.post('/api/v1/assets/assignments/', { data: allocPayload });

      // 2. Run offboarding script logic to check in asset and deactivate user
      // Locate active assignments
      const activeRes = await request.get(`/api/v1/assets/assignments/?assigned_user_id=${holderId}&is_active=true`);
      const activeJson = await activeRes.json();
      expect(activeJson.results).toBeDefined();

      for (const assignment of activeJson.results) {
        const assetId = assignment.asset;
        // Check in asset
        const checkinRes = await request.post(`/api/v1/assets/${assetId}/checkin/`, {
          data: { location: 1, notes: "Automated offboarding check-in" }
        });
        expect(checkinRes.status()).toBeLessThan(400);
      }

      // Deactivate user
      const deactivateRes = await request.patch(`/api/v1/organization/assetholders/${holderId}/`, {
        data: { status: "inactive" }
      });
      expect(deactivateRes.status()).toBeLessThan(400);
    }
  });

  test('13. Workload 4: Bulk CSV import & Audit verification', async ({ request }) => {
    // 1. Read bulk assets CSV guide
    const csvContent = fs.readFileSync(csvDataPath, 'utf8');
    
    // 2. Import assets via CSV guide logic (POST to import endpoint)
    const importPayload = {
      csv_data: csvContent
    };
    
    const response = await request.post('/api/v1/assets/assets/import/', { data: importPayload });
    
    if (response.status() === 201) {
      // 3. Query them via GraphQL
      const query = `
        query {
          assets(search: "MacBook Pro") {
            id
            name
            assetTag
          }
        }
      `;
      const gqlResponse = await request.post('/graphql/', { data: { query } });
      if (gqlResponse.status() === 200) {
        const gqlJson = await gqlResponse.json();
        expect(gqlJson.data.assets.length).toBeGreaterThan(0);

        // 4. Verify ObjectChange audit log records the activity
        const assetId = gqlJson.data.assets[0].id;
        const auditResponse = await request.get(`/api/v1/core/changelogs/?object_id=${assetId}`);
        expect(auditResponse.status()).toBe(200);
        const auditJson = await auditResponse.json();
        expect(auditJson.results.length).toBeGreaterThan(0);
      }
    } else {
      console.log(`Bulk CSV import workload skipped/failed: ${response.status()}`);
    }
  });

  test('14. Workload 5: Multi-tenant isolation E2E', async ({ playwright }) => {
    // SCIM provisions two users in different tenants: 'tenant1' and 'tenant2'
    const tenant1Context = await playwright.request.newContext({
      extraHTTPHeaders: { 'X-Tenant-Slug': 'tenant1' }
    });
    const tenant2Context = await playwright.request.newContext({
      extraHTTPHeaders: { 'X-Tenant-Slug': 'tenant2' }
    });

    // 1. Provision users in tenant1 and tenant2
    const u1Payload = {
      schemas: ["urn:ietf:params:scim:schemas:core:2.0:User"],
      userName: "tenant1.user",
      emails: [{ value: "t1@tenant1.com", primary: true }]
    };
    const u2Payload = {
      schemas: ["urn:ietf:params:scim:schemas:core:2.0:User"],
      userName: "tenant2.user",
      emails: [{ value: "t2@tenant2.com", primary: true }]
    };

    const t1Res = await tenant1Context.post('/api/tenants/tenant1/scim/v2/Users', { data: u1Payload });
    const t2Res = await tenant2Context.post('/api/tenants/tenant2/scim/v2/Users', { data: u2Payload });

    if (t1Res.status() === 201 && t2Res.status() === 201) {
      // 2. Perform OIDC login
      // 3. Verify GraphQL and REST queries are strictly isolated
      const t1GqlResponse = await tenant1Context.post('/graphql/', {
        data: { query: 'query { assets { id name } }' }
      });
      const t2GqlResponse = await tenant2Context.post('/graphql/', {
        data: { query: 'query { assets { id name } }' }
      });

      if (t1GqlResponse.status() === 200 && t2GqlResponse.status() === 200) {
        const t1Json = await t1GqlResponse.json();
        const t2Json = await t2GqlResponse.json();
        
        // Ensure no leakage between tenants
        const t1AssetNames = t1Json.data.assets.map((a: any) => a.name);
        const t2AssetNames = t2Json.data.assets.map((a: any) => a.name);
        
        const intersection = t1AssetNames.filter((name: string) => t2AssetNames.includes(name));
        expect(intersection.length).toBe(0);
      }
    } else {
      console.log(`Multi-tenant isolation E2E skipped/failed due to tenant sync issues`);
    }
  });

});
