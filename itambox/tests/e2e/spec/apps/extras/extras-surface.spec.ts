import { test } from '../../fixtures/test';
import { openOwnedSurface } from '../../helpers/app-surface';

test.describe('extras-owned qualification surface', { tag: '@pr' }, () => {
  test('tag list is reachable inside the attested tenant', async ({ page }) => {
    await openOwnedSurface(page, '/extras/tags/', /Tags/i);
  });
});
