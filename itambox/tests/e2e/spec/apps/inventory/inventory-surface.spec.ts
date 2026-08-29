import { test } from '../../fixtures/test';
import { openOwnedSurface } from '../../helpers/app-surface';

test.describe('inventory-owned qualification surface', { tag: '@pr' }, () => {
  test('inventory list is reachable inside the attested tenant', async ({ page }) => {
    await openOwnedSurface(page, '/inventory/', /Inventory/i);
  });
});
