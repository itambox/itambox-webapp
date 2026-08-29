import { test } from '../../../fixtures/test';
import { openOwnedSurface } from '../../../helpers/app-surface';

test.describe('software-owned qualification surface', { tag: '@pr' }, () => {
  test('software list is reachable inside the attested tenant', async ({ page }) => {
    await openOwnedSurface(page, '/software/software/', /Software/i);
  });
});
