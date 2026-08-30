import { expect, type Locator, type Page, type Response } from '@playwright/test';

export type SubmitExpectation = {
  submit: Locator;
  method?: string;
  path: string;
  expectedStatus: number;
};

/** Submit one exact action and assert its transport response. */
export async function submitAndExpect(page: Page, expectation: SubmitExpectation): Promise<Response> {
  const method = expectation.method || 'POST';
  const responsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === method && url.pathname === expectation.path;
  });
  await expectation.submit.click();
  const response = await responsePromise;
  const body = await response.text();
  expect(response.status(), `${method} ${expectation.path}: ${body}`).toBe(expectation.expectedStatus);
  return response;
}

/** Select a known Tom Select option beneath an exact page/form root. */
export async function selectTomOption(
  root: Page | Locator,
  field: string,
  value: string,
): Promise<void> {
  const select = root.locator(`select[data-tom-select][name="${field}"]`);
  await expect(select, `Tom Select field ${field}`).toHaveCount(1);
  if ((await select.inputValue()) === value) {
    await expect(select).toHaveValue(value);
    return;
  }
  const option = select.locator(`option[value="${value}"]`);
  await expect(option, `Tom Select option ${field}=${value}`).toHaveCount(1);
  const label = (await option.textContent())?.trim();
  if (!label) throw new Error(`Tom Select option ${field}=${value} has no label.`);

  const wrapper = select.locator('xpath=following-sibling::div[contains(@class,"ts-wrapper")][1]');
  await expect(wrapper, `Tom Select wrapper for ${field}`).toHaveCount(1);
  await wrapper.locator('.ts-control').click();
  const visibleOption = wrapper.locator(`.ts-dropdown .option[data-value="${value}"]:visible`);
  await expect(visibleOption, `visible Tom Select option ${field}=${value}`).toHaveCount(1);
  await visibleOption.click();
  await expect(select).toHaveValue(value);
}
