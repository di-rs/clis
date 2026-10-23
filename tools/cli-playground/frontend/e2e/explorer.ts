import { expect, type Page } from "@playwright/test";

export async function openExplorer(page: Page) {
  const toggle = page.getByRole("button", { name: "Explore", exact: true });
  if ((await toggle.isVisible()) && (await toggle.getAttribute("aria-expanded")) === "false") {
    await toggle.click();
  }
  await expect(page.getByRole("combobox", { name: "CLI", exact: true })).toBeVisible();
}
export async function openSampleFiles(page: Page) {
  await openExplorer(page);
  const section = page.locator(".workspace-details");
  if (!(await section.getAttribute("open"))) {
    // Empty open attributes are truthy only in DOM, not string coercion.
    if (!(await section.evaluate((node) => (node as HTMLDetailsElement).open)))
      await section.locator("summary").click();
  }
}
