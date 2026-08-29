import { test } from '../../../fixtures/test';
import { openOwnedSurface } from '../../../helpers/app-surface';

test.describe('procurement-owned qualification surface', { tag: '@pr' }, () => {
  test('purchase order list is reachable inside the attested tenant', async ({ page }) => {
    await openOwnedSurface(page, '/procurement/orders/', /Purchase Orders/i);
  });
});
