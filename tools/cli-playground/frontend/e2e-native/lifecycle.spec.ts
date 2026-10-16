import { expect, test } from "@playwright/test";
import { nativeArchive } from "./archive";

test("native shell and same-page reconnect preserve the admitted session", async ({
  page,
  request,
}) => {
  const binary = process.env.BOXIE_NATIVE_BINARY;
  const token = process.env.BOXIE_NATIVE_DEPLOY_TOKEN;
  if (!binary || !token)
    throw new Error("native binary and fixture deployment credential required");
  const archive = nativeArchive(binary);
  const published = await request.post("/api/environments?pr=43210&sha=" + "a".repeat(40), {
    headers: { Authorization: "Bearer " + token, "Content-Type": "application/zip" },
    data: archive,
  });
  expect(published.ok()).toBeTruthy();
  const environment = await published.json();
  await page.addInitScript(() => {
    const sockets: WebSocket[] = [];
    Object.assign(window, { nativeSockets: sockets });
    const Native = window.WebSocket;
    window.WebSocket = class extends Native {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols);
        sockets.push(this);
      }
    };
  });
  try {
    await page.goto(`/p/${environment.id}`);
    await expect(page.locator("#connection-status")).toHaveText("Connected", { timeout: 45_000 });
    const before = await page.evaluate(
      () => (window as unknown as { nativeSockets: WebSocket[] }).nativeSockets[0]!.url,
    );
    await page
      .locator(".xterm-helper-textarea")
      .pressSequentially("echor 'native-'\"browser-ok\"\n");
    await expect(page.locator(".xterm-rows")).toContainText("native-browser-ok");
    await page.evaluate(() =>
      (window as unknown as { nativeSockets: WebSocket[] }).nativeSockets.at(-1)!.close(),
    );
    await expect(page.locator("#connection-status")).toHaveText("Disconnected");
    await page.getByRole("button", { name: "Reconnect", exact: true }).click();
    await expect
      .poll(() =>
        page.evaluate(
          () => (window as unknown as { nativeSockets: WebSocket[] }).nativeSockets.length,
        ),
      )
      .toBeGreaterThan(1);
    await expect(page.locator("#connection-status")).toHaveText("Connected");
    const after = await page.evaluate(
      () => (window as unknown as { nativeSockets: WebSocket[] }).nativeSockets.at(-1)!.url,
    );
    expect(new URL(after).searchParams.get("session_id")).toBe(
      new URL(before).searchParams.get("session_id"),
    );
    await page.setViewportSize({ width: 900, height: 700 });
    await page.locator(".xterm-helper-textarea").pressSequentially("echor 'after-'\"reconnect\"\n");
    await expect(page.locator(".xterm-rows")).toContainText("after-reconnect");
    await page.locator(".xterm-helper-textarea").pressSequentially("sleep 30\n");
    await page.locator(".xterm-helper-textarea").press("Control+c");
    await page.locator(".xterm-helper-textarea").pressSequentially("echor 'after-'\"interrupt\"\n");
    await expect(page.locator(".xterm-rows")).toContainText("after-interrupt");
  } finally {
    const closed = await request.delete("/api/pull-requests/43210", {
      headers: { Authorization: "Bearer " + token },
    });
    expect(closed.ok()).toBeTruthy();
    await page.close();
  }
});
