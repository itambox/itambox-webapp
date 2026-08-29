import { test } from '../../fixtures/test';
import { openOwnedSurface } from '../../helpers/app-surface';

test.describe('compliance-owned qualification surface', { tag: '@pr' }, () => {
  test('audit-session list is reachable inside the attested tenant', async ({ page }) => {
    await openOwnedSurface(page, '/compliance/audit-sessions/', /Audit Sessions/i);
  });
});
