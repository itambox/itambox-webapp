import { test } from '../../fixtures/test';
import { openOwnedSurface } from '../../helpers/app-surface';

test.describe('licenses-owned qualification surface', { tag: '@pr' }, () => {
  test('license list is reachable inside the attested tenant', async ({ page }) => {
    await openOwnedSurface(page, '/licenses/', /Licenses/i);
  });
});
