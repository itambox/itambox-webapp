import { test } from '../../../fixtures/test';
import { openOwnedSurface } from '../../../helpers/app-surface';

test.describe('organization-owned qualification surface', { tag: '@pr' }, () => {
  test('sites list is reachable inside the attested tenant', async ({ page }) => {
    await openOwnedSurface(page, '/organization/sites/', /Sites/i);
  });
});
