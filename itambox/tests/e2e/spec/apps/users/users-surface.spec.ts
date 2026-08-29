import { test } from '../../../fixtures/test';
import { openOwnedSurface } from '../../../helpers/app-surface';

test.describe('users-owned qualification surface', { tag: '@pr' }, () => {
  test('profile is reachable inside the attested tenant', async ({ page }) => {
    await openOwnedSurface(page, '/users/profile/', /Profile/i);
  });
});
