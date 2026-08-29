import { test } from '../../../fixtures/test';
import { openOwnedSurface } from '../../../helpers/app-surface';

test.describe('core-owned qualification surface', { tag: '@pr' }, () => {
  test('search surface is reachable inside the attested tenant', async ({ page }) => {
    await openOwnedSurface(page, '/search/', /Search/i);
  });
});
