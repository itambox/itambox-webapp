import { chromium, FullConfig } from '@playwright/test';

async function globalSetup(config: FullConfig) {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  // Navigate to login page
  await page.goto('http://localhost:8000/');
  
  // Wait for the username input to be visible
  try {
    await page.waitForSelector('input[name="username"]', { timeout: 5000 });
    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', 'admin123');
    await Promise.all([
      page.waitForNavigation(),
      page.click('button[type="submit"]')
    ]);
  } catch (e) {
    console.log('Login form not found, maybe already logged in or different URL structure.');
  }

  // Save auth state
  await page.context().storageState({ path: 'storageState.json' });
  await browser.close();
}

export default globalSetup;
