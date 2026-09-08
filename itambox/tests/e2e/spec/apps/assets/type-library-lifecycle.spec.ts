import { readFile } from 'node:fs/promises';

import type { Page } from '@playwright/test';

import { test, expect } from '../../../fixtures/test';


type LibrarySnapshot = {
  effective_definitions: { fields: Array<{ key: string; label: string }> };
  upstream: { source_document: { definitions: { fields: Array<{ key: string; label: string }> } } };
};

function uniqueNamespace(runId: string): string {
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'run';
  return `e2e-${suffix}`;
}

function releaseDocument(namespace: string, release: number, fieldLabel: string) {
  const suffix = namespace.replace(/[^a-z0-9]+/gi, '_');
  const fieldKey = `${suffix}__state`.slice(0, 64);
  const choiceSet = `${namespace}/state`;
  const fieldIdentity = `${namespace}/${fieldKey}`;
  const fieldset = `${namespace}/specs`;
  const category = `catalog/${namespace}-devices`;
  const manufacturer = `catalog/${namespace}-maker`;
  return {
    schema_version: 1,
    kind: 'itambox.type-library.release',
    library: { namespace, release, label: `Browser Library ${namespace}` },
    requires: [],
    definitions: {
      choice_sets: [
        {
          id: choiceSet,
          label: 'State',
          description: 'Browser journey states',
          lifecycle: 'active',
          choices: [
            { key: 'on', label: 'On', lifecycle: 'active' },
            { key: 'off', label: 'Off', lifecycle: 'active' },
          ],
        },
      ],
      fields: [
        {
          key: fieldKey,
          namespace,
          label: fieldLabel,
          help_text: '',
          targets: ['asset_type'],
          activation: 'composed',
          field_type: 'multi-select',
          required: false,
          nullable: false,
          lifecycle: 'active',
          validation: { max_values: 2 },
          choice_set: choiceSet,
        },
      ],
      fieldsets: [
        {
          id: fieldset,
          label: 'Specs',
          description: 'Browser journey specifications',
          lifecycle: 'active',
          fields: [fieldIdentity],
        },
      ],
      categories: [
        {
          id: category,
          label: 'Browser devices',
          description: 'Browser journey category',
          lifecycle: 'active',
          applies_to: ['asset'],
          default_fieldsets: [fieldset],
        },
      ],
      manufacturers: [
        {
          id: manufacturer,
          label: 'Browser manufacturer',
          description: 'Browser journey manufacturer',
          lifecycle: 'active',
        },
      ],
      asset_types: [
        {
          id: `${namespace}/device-a`,
          manufacturer,
          model: 'Browser Device',
          part_number: 'E2E-A',
          gtin: null,
          region: '',
          configuration: '',
          category,
          description: 'Browser journey device',
          lifecycle: 'active',
          fieldsets: [fieldset],
          specifications: { [fieldKey]: ['on', 'off'] },
          historical_specifications: {},
        },
      ],
    },
  };
}

async function uploadAndPreview(page: Page, document: object, filename: string) {
  await page.goto('/assets/type-libraries/import/', { waitUntil: 'domcontentloaded' });
  await page.locator('input[name="document"]').setInputFiles({
    name: filename,
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(document), 'utf8'),
  });
  await page.getByRole('button', { name: 'Validate and preview' }).click();
  await expect(page.locator('[data-library-preview]')).toBeVisible();
}

test.describe('assets Type Library browser workflow', { tag: '@pr' }, () => {
  test('imports, preserves a local snapshot edit, resolves an update conflict, and exports/reimports', async ({ page, runId }) => {
    const namespace = uniqueNamespace(runId);
    const releaseOne = releaseDocument(namespace, 1, 'State');
    const releaseTwo = releaseDocument(namespace, 2, 'Upstream State');

    const listResponse = await page.goto('/assets/type-libraries/', { waitUntil: 'domcontentloaded' });
    expect(listResponse?.status(), 'GET Type Library list').toBe(200);
    await page.locator('a.btn-primary[href="/assets/type-libraries/import/"]').click();

    await page.locator('input[name="document"]').setInputFiles({
      name: 'release-1.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(releaseOne), 'utf8'),
    });
    await page.getByRole('button', { name: 'Validate and preview' }).click();
    await expect(page.locator('[data-library-preview]')).toContainText(namespace);
    await expect(page.locator('text=Preview is not an apply.')).toBeVisible();
    await expect(page.locator('[data-library-apply]')).toBeEnabled();
    await page.getByRole('button', { name: 'Apply Library' }).click();
    await expect(page.locator('[data-library-apply-result]')).toContainText('applied successfully');

    await page.goto('/assets/type-libraries/', { waitUntil: 'domcontentloaded' });
    const detailLink = page.getByRole('link', { name: namespace, exact: true });
    await expect(detailLink).toHaveCount(1);
    await detailLink.click();
    await expect(page.getByRole('heading', { name: namespace })).toBeVisible();
    await expect(page.locator('p.text-muted.small').filter({ hasText: 'Tenant Asset values' })).toBeVisible();

    const exportForm = page.locator('form[action*="/export/"]');
    await exportForm.locator('select[name="mode"]').selectOption('effective_snapshot');
    await exportForm.locator('input[name="acknowledge_retained_history"]').check();
    const downloadPromise = page.waitForEvent('download');
    await exportForm.getByRole('button', { name: 'Download export' }).click();
    const download = await downloadPromise;
    const downloadPath = await download.path();
    expect(downloadPath, 'effective snapshot download path').not.toBeNull();
    const localSnapshot = JSON.parse(await readFile(downloadPath!, 'utf8')) as LibrarySnapshot;
    const fieldKey = releaseOne.definitions.fields[0].key;
    const localField = localSnapshot.effective_definitions.fields.find(field => field.key === fieldKey);
    expect(localField, 'exported stable field identity').toBeDefined();
    localField!.label = 'Local Browser Override';

    await uploadAndPreview(page, localSnapshot, 'effective-snapshot-local-edit.json');
    await expect(page.locator('[data-library-apply]')).toBeEnabled();
    await page.getByRole('button', { name: 'Apply Library' }).click();
    await expect(page.locator('[data-library-apply-result]')).toContainText('applied successfully');

    await uploadAndPreview(page, releaseTwo, 'release-2.json');
    await expect(page.locator('[data-library-blocking-conflict]')).toBeVisible();
    await expect(page.locator('[data-library-apply]')).toBeDisabled();
    await expect(page.locator('select[name^="resolution_"]').first()).toHaveValue('abort');
    await page.locator('select[name^="resolution_"]').first().selectOption('take_upstream');
    await page.getByRole('button', { name: 'Review choices' }).click();
    await expect(page.locator('[data-library-blocking-conflict]')).toHaveCount(0);
    await expect(page.locator('[data-library-apply]')).toBeEnabled();
    await page.getByRole('button', { name: 'Apply Library' }).click();
    await expect(page.locator('[data-library-apply-result]')).toContainText('applied successfully');

    await page.goto('/assets/type-libraries/', { waitUntil: 'domcontentloaded' });
    await page.getByRole('link', { name: namespace, exact: true }).click();
    await expect(page.getByText('Immutable release history')).toBeVisible();
    await expect(page.locator('p.text-muted.small').filter({ hasText: 'Tenant Asset values' })).toBeVisible();
    const finalExport = page.locator('form[action*="/export/"]');
    await finalExport.locator('select[name="mode"]').selectOption('effective_snapshot');
    await finalExport.locator('input[name="acknowledge_retained_history"]').check();
    const finalDownloadPromise = page.waitForEvent('download');
    await finalExport.getByRole('button', { name: 'Download export' }).click();
    const finalDownload = await finalDownloadPromise;
    const finalPath = await finalDownload.path();
    expect(finalPath).not.toBeNull();
    const acceptedSnapshot = JSON.parse(await readFile(finalPath!, 'utf8')) as LibrarySnapshot;
    expect(acceptedSnapshot.effective_definitions.fields.find(field => field.key === fieldKey)?.label).toBe('Upstream State');
    expect(acceptedSnapshot.upstream.source_document.definitions.fields.find(field => field.key === fieldKey)?.label).toBe('Upstream State');
    await uploadAndPreview(page, acceptedSnapshot, 'effective-snapshot-reimport.json');
    await page.getByRole('button', { name: 'Apply Library' }).click();
    await expect(page.locator('[data-library-apply-result]')).toContainText('no changes were applied');
    await page.goto('/assets/type-libraries/', { waitUntil: 'domcontentloaded' });
    await page.getByRole('link', { name: namespace, exact: true }).click();
    await expect(page.locator('[aria-labelledby="release-history-title"] tbody tr')).toHaveCount(2);
  });
});
