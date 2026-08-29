import { test } from '../../../fixtures/test';
import { openOwnedSurface } from '../../../helpers/app-surface';

test.describe('subscriptions-owned qualification surface', { tag: '@pr' }, () => {
  test('subscription list is reachable inside the attested tenant', async ({ page }) => {
    await openOwnedSurface(page, '/subscriptions/subscriptions/', /Subscriptions/i);
  });
});
