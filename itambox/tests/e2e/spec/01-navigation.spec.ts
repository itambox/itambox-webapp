import { test, expect } from '@playwright/test';

test.describe('Authentication & Navigation Fluidity', () => {
  
  test.beforeEach(async ({ page }) => {
    // Listen for unhandled exceptions or console errors
    page.on('console', msg => {
      if (msg.type() === 'error') {
        console.error(`[Console Error]: ${msg.text()}`);
      }
    });
    page.on('pageerror', error => {
      console.error(`[Page Error]: ${error.message}`);
    });
  });

  test('Navigate through all main sidebar links and test breadcrumbs', async ({ page }) => {
    await page.goto('/');

    // Wait for the sidebar to load
    await page.waitForSelector('.navbar-nav');
    
    // Find all links in the sidebar
    const navLinks = page.locator('.navbar-nav .nav-link:not(.dropdown-toggle)');
    const count = await navLinks.count();
    
    console.log(`Found ${count} sidebar navigation links.`);
    
    // Test a subset to avoid excessive runtime or timeouts, but iterate through them
    for (let i = 0; i < count; i++) {
      const link = navLinks.nth(i);
      const href = await link.getAttribute('href');
      
      // Ignore empty links or external links
      if (!href || href === '#' || href.startsWith('http')) continue;

      console.log(`Navigating to ${href}`);
      
      const response = await page.goto(href);
      
      // Ensure we don't get 404s or 500s
      expect(response?.status()).toBeLessThan(400);

      // Check if breadcrumbs are present and functional (just ensuring they exist)
      const breadcrumbs = page.locator('.breadcrumb');
      if (await breadcrumbs.count() > 0) {
        // Try clicking the first breadcrumb (often 'Home' or parent)
        const firstCrumb = breadcrumbs.locator('li a').first();
        if (await firstCrumb.count() > 0) {
           await firstCrumb.click();
           await expect(page).not.toHaveTitle(/Error|404/);
        }
      }
      
      // Go back to base for next iteration
      await page.goto('/');
    }
  });

});
