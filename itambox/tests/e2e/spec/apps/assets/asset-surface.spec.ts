import { test } from '../../fixtures/test';
import { openOwnedSurface } from '../../helpers/app-surface';

test.describe('assets-owned qualification surface', { tag: '@pr' }, () => {
  test('asset list is reachable inside the attested tenant', async ({ page }) => {
    await openOwnedSurface(page, '/assets/assets/', /Assets/i);
  });
});
