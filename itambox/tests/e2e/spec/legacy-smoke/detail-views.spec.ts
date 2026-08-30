import { test, expect } from '@playwright/test';

test.describe('Object Detail Views & Feature Tab Integrity', () => {

  test('Open detail view, test tabs and journal', async ({ page }) => {
    // Navigate to a list view and click the first item to get to a detail view
    await page.goto('/inventory/accessories/');
    
    const firstItemLink = page.locator('table tbody tr td a').first();
    if (await firstItemLink.count() === 0) {
       console.log('No items to view details for.');
       return;
    }
    
    await firstItemLink.click();
    
    // We should be on a detail view now
    // Wait for the page layout
    await page.waitForSelector('.page-body');
    
    // Interactivity-test every sub-navigation tab panel
    // Usually Tabler tabs are .nav-tabs .nav-link
    const tabs = page.locator('.nav-tabs .nav-link');
    const tabCount = await tabs.count();
    
    for (let i = 0; i < tabCount; i++) {
       const tab = tabs.nth(i);
       await tab.click();
       // If it's an HTMX tab, wait for network
       await page.waitForTimeout(500); // small delay to allow swap
       
       // Ensure active class
       await expect(tab).toHaveClass(/active/);
    }
    
    // Try posting a manual update directly inside the Journal/Comments timeline box if it exists
    const journalTab = page.locator('.nav-tabs .nav-link', { hasText: 'Journal' }).or(page.locator('.nav-tabs .nav-link', { hasText: 'Notes' })).first();
    if (await journalTab.count() > 0) {
       await journalTab.click();
       await page.waitForTimeout(1000);
       
       const commentInput = page.locator('textarea[name="notes"], textarea[name="comment"], textarea[name="body"]').first();
       if (await commentInput.count() > 0) {
          await commentInput.fill('Automated Test Note Playwright');
          
          const submitBtn = page.locator('button', { hasText: 'Post' }).or(page.locator('button', { hasText: 'Save' })).first();
          if (await submitBtn.count() > 0) {
             await submitBtn.click();
             
             // Wait for HTMX to append the note
             await page.waitForResponse(response => response.status() === 200 || response.status() === 201);
             
             // Verify it appears
             await expect(page.locator('body')).toContainText('Automated Test Note Playwright');
          }
       }
    }
  });

});
