import { Buffer } from "node:buffer";
import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";

const environmentId = "0123456789abcdef0123456789abcdef";
const previewPath = `/p/${environmentId}`;
const metadataPath = `/api/environments/${environmentId}`;
const metadata = {
  id: environmentId,
  clis: ["catr"],
  pr: 42,
  sha: "0123456789abcdef0123456789abcdef01234567",
  revision: "0123456789abcdef0123456789abcdef01234567",
  build: null,
  expires_at: "2099-01-01T00:00:00Z",
};

const currentBuild = {
  sha: metadata.sha,
  run_id: 501,
  run_number: 12,
  run_attempt: 1,
  state: "ready",
};

type ClientMessage =
  | { type: "auth"; credential: string }
  | { type: "input"; data: string }
  | { type: "resize"; cols: number; rows: number };

interface Session {
  socket: WebSocketRoute;
  messages: ClientMessage[];
  closed: boolean;
  deleted: boolean;
}

async function mockTerminal(page: Page, identifier = environmentId): Promise<Session[]> {
  const sessions: Session[] = [];
  let created = 0;
  await page.route(`**/api/environments/${identifier}/sessions`, async (route) => {
    const { revision } = route.request().postDataJSON() as { revision: string };
    created++;
    await route.fulfill({
      status: 201,
      json: {
        id: `00000000-0000-4000-8000-${String(created).padStart(12, "0")}`,
        preview_id: identifier,
        revision,
        credential: "a".repeat(43),
        absolute_deadline: "2099-01-01T01:00:00Z",
        state: "ready",
        reason: null,
      },
    });
  });
  await page.route(`**/api/environments/${identifier}/sessions/*`, async (route) => {
    expect(route.request().method()).toBe("DELETE");
    expect(route.request().headers()["authorization"]).toBe(`Bearer ${"a".repeat(43)}`);
    const id = route.request().url().split("/").at(-1);
    const session = sessions.find(
      (item) => new URL(item.socket.url()).searchParams.get("session_id") === id,
    );
    if (session) session.deleted = true;
    await route.fulfill({ json: { state: "terminating" } });
  });
  await page.routeWebSocket(
    (url) => url.pathname === `/ws/${identifier}`,
    (socket) => {
      const session: Session = { socket, messages: [], closed: false, deleted: false };
      socket.onMessage((message) => {
        expect(typeof message).toBe("string");
        expect(message.length).toBeLessThanOrEqual(16_384);
        expect(Buffer.byteLength(message)).toBeLessThanOrEqual(16_384);
        // The production client owns this protocol; the double records its frames.
        const data = JSON.parse(message.toString()) as ClientMessage;
        if (session.messages.length === 0) {
          expect(data).toEqual({ type: "auth", credential: "a".repeat(43) });
          expect(socket.url()).not.toContain("a".repeat(43));
          socket.send(
            JSON.stringify({
              type: "authenticated",
              state: "ready",
              absolute_deadline: "2099-01-01T01:00:00Z",
              reason: null,
            }),
          );
        }
        session.messages.push(data);
      });
      socket.onClose(() => {
        session.closed = true;
      });
      sessions.push(session);
    },
  );
  return sessions;
}

function inputs(session: Session): string[] {
  return session.messages.flatMap((message) => (message.type === "input" ? [message.data] : []));
}

async function openPlayground(page: Page): Promise<Session[]> {
  await page.route(`**${metadataPath}`, (route) => route.fulfill({ json: metadata }));
  const sessions = await mockTerminal(page);
  await page.goto(previewPath);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  await expect.poll(() => sessions.length).toBe(1);
  return sessions;
}

test("loads the production terminal, fits the viewport, and inserts commands without executing", async ({
  page,
}) => {
  const sessions = await openPlayground(page);
  const session = sessions[0];
  expect(new URL(session.socket.url()).searchParams.get("revision")).toBe(metadata.sha);
  await expect(page).toHaveTitle("catr / CLI playground");
  await expect(page.locator("#meta-pr")).toHaveText("#42");
  await expect(page.locator("#session-expiry")).toHaveAttribute("datetime", "2099-01-01T01:00:00Z");
  expect(
    await page.evaluate(() =>
      [...Object.values(localStorage), ...Object.values(sessionStorage)].join(""),
    ),
  ).not.toContain("a".repeat(43));
  await expect(page.locator("#meta-sha")).toHaveText("0123456");
  await expect
    .poll(() =>
      session.messages.some(
        (message) => message.type === "resize" && message.cols > 0 && message.rows > 0,
      ),
    )
    .toBe(true);

  session.socket.send(Buffer.from("Native terminal output\r\n", "utf8"));
  await expect(page.locator(".xterm-accessibility-tree")).toContainText("Native terminal output");
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true);

  await page.getByRole("button", { name: "Insert catr --help: Explore the options" }).click();
  await expect.poll(() => inputs(session)).toEqual(["/opt/artifact/catr --help"]);
  await page.keyboard.press("Enter");
  await expect.poll(() => inputs(session)).toEqual(["/opt/artifact/catr --help", "\r"]);

  await page.getByRole("button", { name: "Insert command to read welcome.txt" }).click();
  await expect
    .poll(() => inputs(session))
    .toEqual(["/opt/artifact/catr --help", "\r", "cat welcome.txt"]);

  await page.locator("summary").click();
  await page.getByRole("button", { name: "Insert cat welcome.txt | wc -l: Pipe output" }).click();
  await expect
    .poll(() => inputs(session))
    .toEqual(["/opt/artifact/catr --help", "\r", "cat welcome.txt", "cat welcome.txt | wc -l"]);

  await page.keyboard.type(" && printf 'manual'");
  await expect
    .poll(() => inputs(session).join(""))
    .toBe("/opt/artifact/catr --help\rcat welcome.txtcat welcome.txt | wc -l && printf 'manual'");
});

for (const count of [3, 5, 10]) {
  test(`selects among ${count} CLIs within the same PR terminal and URL`, async ({ page }) => {
    const clis = ["catr", ...Array.from({ length: count - 1 }, (_, index) => `tool${index + 1}`)];
    const target = clis[count - 1];
    await page.route(`**${metadataPath}`, (route) =>
      route.fulfill({ json: { ...metadata, clis } }),
    );
    const sessions = await mockTerminal(page);
    await page.goto(previewPath);
    await expect(page.locator("#connection-status")).toHaveText("Connected");
    const session = sessions[0];
    session.socket.send(Buffer.from("Existing workspace output\r\n", "utf8"));
    const output = page.locator(".xterm-accessibility-tree");
    await expect(output).toContainText("Existing workspace output");
    const picker = page.getByRole("combobox", { name: "CLI", exact: true });
    await expect(picker.locator("option")).toHaveCount(count);
    await expect(picker).toHaveValue("catr");
    await picker.selectOption(target);
    await expect(page).toHaveURL(previewPath);
    await expect(page).toHaveTitle(`${target} / CLI playground`);
    await expect(output).toContainText("Existing workspace output");
    await expect(picker).toHaveValue(target);
    await page
      .getByRole("button", { name: `Insert ${target} --help: Explore the options` })
      .click();
    await expect.poll(() => inputs(session)).toEqual([`/opt/artifact/${target} --help`]);
    expect(session.closed).toBe(false);
    expect(sessions).toHaveLength(1);
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
      .toBe(true);
  });
}

test("pastes a large Unicode document through bounded messages in order", async ({ page }) => {
  const [session] = await openPlayground(page);
  session.socket.send(Buffer.from("\x1b[?2004hReady to paste", "utf8"));
  await expect(page.locator(".xterm-accessibility-tree")).toContainText("Ready to paste");
  const text = '漢🙂\t"\\'.repeat(5_000);
  await page.locator(".xterm-helper-textarea").evaluate((element, value) => {
    const clipboardData = new DataTransfer();
    clipboardData.setData("text/plain", value);
    element.dispatchEvent(
      new ClipboardEvent("paste", { clipboardData, bubbles: true, cancelable: true }),
    );
  }, text);
  await expect.poll(() => inputs(session).join("")).toBe(`\x1b[200~${text}\x1b[201~`);
  expect(inputs(session).length).toBeGreaterThan(1);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
});

for (const action of ["Reset session", "Start new session"]) {
  test(`${action} refreshes the revision at the same PR URL`, async ({ page }) => {
    const sessions = await openPlayground(page);
    await page.route(`**${metadataPath}`, (route) =>
      route.fulfill({
        json: {
          ...metadata,
          sha: "b".repeat(40),
          revision: "b".repeat(40),
          clis: ["echor", "wcr"],
        },
      }),
    );
    if (action === "Start new session") {
      await page.getByRole("button", { name: "Disconnect", exact: true }).click();
    }
    await page.getByRole("button", { name: action, exact: true }).click();
    await expect(page.locator("#meta-sha")).toHaveText("bbbbbbb");
    await expect(page.getByRole("combobox", { name: "CLI", exact: true })).toHaveValue("echor");
    await expect(
      page.getByRole("button", { name: "Insert echor --help: Explore the options" }),
    ).toBeEnabled();
    await expect(page).toHaveURL(previewPath);
    await expect.poll(() => sessions.length).toBe(2);
    expect(new URL(sessions[1].socket.url()).searchParams.get("revision")).toBe("b".repeat(40));
    expect(sessions[0].closed).toBe(true);
    await expect.poll(() => sessions[0].deleted).toBe(true);
  });
}

test("a rejected stale revision can reload metadata and reconnect at the same URL", async ({
  page,
}) => {
  const sessions = await openPlayground(page);
  await page.route(`**${metadataPath}`, (route) =>
    route.fulfill({
      json: { ...metadata, sha: "b".repeat(40), revision: "b".repeat(40) },
    }),
  );
  await sessions[0].socket.close({ code: 1008, reason: "Revision changed" });
  await expect(page.locator("#connection-status")).toHaveText("Disconnected");
  await page.getByRole("button", { name: "Start new session" }).click();
  await expect(page.locator("#meta-sha")).toHaveText("bbbbbbb");
  await expect.poll(() => sessions.length).toBe(2);
  expect(new URL(sessions[1].socket.url()).searchParams.get("revision")).toBe("b".repeat(40));
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  await expect(page).toHaveURL(previewPath);
});

test("a new build is discovered without replacing the current terminal, including same-SHA reruns", async ({
  page,
}) => {
  await page.clock.install();
  let latest = { ...metadata, revision: `${metadata.sha}-12-1`, build: currentBuild };
  await page.route(`**${metadataPath}`, (route) => route.fulfill({ json: latest }));
  const sessions = await mockTerminal(page);
  await page.goto(previewPath);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  const first = sessions[0];
  expect(new URL(first.socket.url()).searchParams.get("revision")).toBe(`${metadata.sha}-12-1`);
  first.socket.send(Buffer.from("My work is still here\r\n"));
  latest = {
    ...latest,
    revision: `${metadata.sha}-12-2`,
    build: { ...currentBuild, run_attempt: 2 },
  };
  await page.clock.fastForward(10_000);
  await expect(page.getByRole("button", { name: "Switch to latest build" })).toBeVisible();
  await expect(page.locator(".xterm-accessibility-tree")).toContainText("My work is still here");
  expect(first.closed).toBe(false);
  expect(sessions).toHaveLength(1);
  await page.getByRole("button", { name: "Switch to latest build" }).click();
  await expect.poll(() => sessions.length).toBe(2);
  await expect.poll(() => first.closed).toBe(true);
  expect(new URL(sessions[1].socket.url()).searchParams.get("revision")).toBe(
    `${metadata.sha}-12-2`,
  );
  await expect(page.locator(".xterm-accessibility-tree")).not.toContainText(
    "My work is still here",
  );
});

test("a first build opens its terminal when ready without reloading the page", async ({ page }) => {
  await page.clock.install();
  let ready = false;
  await page.route(`**${metadataPath}`, (route) =>
    route.fulfill({
      json: ready
        ? { ...metadata, revision: `${metadata.sha}-12-1`, build: currentBuild }
        : {
            ...metadata,
            sha: null,
            revision: null,
            clis: [],
            build: { ...currentBuild, state: "building" },
          },
    }),
  );
  const sessions = await mockTerminal(page);
  await page.goto(previewPath);
  await expect(page.getByRole("heading", { name: "This preview is building." })).toBeVisible();
  expect(sessions).toHaveLength(0);
  ready = true;
  await page.clock.fastForward(10_000);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  expect(sessions).toHaveLength(1);
});

for (const [state, title] of [
  ["failed", "The latest preview failed."],
  ["empty", "No affected CLIs."],
  ["cancelled", "The latest build was cancelled."],
]) {
  test(`${state} without a runnable artifact explains the state without a terminal`, async ({
    page,
  }) => {
    await page.route(`**${metadataPath}`, (route) =>
      route.fulfill({
        json: {
          ...metadata,
          sha: null,
          revision: null,
          clis: [],
          build: { ...currentBuild, state },
        },
      }),
    );
    const sessions = await mockTerminal(page);
    await page.goto(previewPath);
    await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
    expect(sessions).toHaveLength(0);
  });
}

test("a failed newer build labels the retained session and polling errors leave it usable", async ({
  page,
}) => {
  await page.clock.install();
  let failRequest = false;
  let latest = { ...metadata, revision: `${metadata.sha}-12-1`, build: currentBuild };
  await page.route(`**${metadataPath}`, (route) =>
    route.fulfill({
      status: failRequest ? 503 : 200,
      json: latest,
    }),
  );
  const sessions = await mockTerminal(page);
  await page.goto(previewPath);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  latest = {
    ...latest,
    build: { ...currentBuild, sha: "b".repeat(40), run_number: 13, state: "failed" },
  };
  await page.clock.fastForward(10_000);
  await expect(page.locator("#build-banner")).toContainText("Latest preview failed");
  await expect(page.locator("#build-banner")).toContainText("bbbbbbb");
  await expect(page.locator("#build-banner")).toContainText("last successful build");
  await expect(page.locator("#meta-sha")).toHaveText("0123456");
  failRequest = true;
  await page.clock.fastForward(10_000);
  await page.getByRole("button", { name: "Insert catr --help: Explore the options" }).click();
  await expect.poll(() => inputs(sessions[0])).toEqual(["/opt/artifact/catr --help"]);
  expect(sessions).toHaveLength(1);
  expect(sessions[0].closed).toBe(false);
});

test("a failed head distinguishes the latest successful build from an older active terminal", async ({
  page,
}) => {
  await page.clock.install();
  let latest = { ...metadata, revision: `${metadata.sha}-12-1`, build: currentBuild };
  await page.route(`**${metadataPath}`, (route) => route.fulfill({ json: latest }));
  const sessions = await mockTerminal(page);
  await page.goto(previewPath);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  latest = {
    ...latest,
    sha: "b".repeat(40),
    revision: `${"b".repeat(40)}-13-1`,
    build: { ...currentBuild, sha: "c".repeat(40), run_number: 14, state: "failed" },
  };
  await page.clock.fastForward(10_000);
  await expect(page.locator("#build-banner")).toContainText("last successful build is bbbbbbb");
  await expect(page.locator("#build-banner")).toContainText("Your terminal remains on 0123456");
  await expect(page.getByRole("button", { name: "Switch to latest build" })).toBeVisible();
  expect(sessions).toHaveLength(1);
  expect(sessions[0].closed).toBe(false);
});

test("disconnect retains output and disables input, while reconnect and reset start fresh terminals", async ({
  page,
}) => {
  const sessions = await openPlayground(page);
  const first = sessions[0];
  const output = page.locator(".xterm-accessibility-tree");
  first.socket.send(Buffer.from("First session output\r\n", "utf8"));
  await expect(output).toContainText("First session output");

  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  await expect(page.locator("#connection-status")).toHaveText("Disconnected");
  await expect.poll(() => first.closed).toBe(true);
  await expect(output).toContainText("First session output");
  await expect(page.getByRole("button", { name: "Disconnect", exact: true })).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Insert command to read welcome.txt" }),
  ).toBeDisabled();
  await page.locator(".xterm-helper-textarea").focus();
  await page.keyboard.type("ignored");
  expect(inputs(first)).toEqual([]);

  await page.getByRole("button", { name: "Start new session" }).click();
  await expect.poll(() => sessions.length).toBe(2);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  await expect(output).not.toContainText("First session output");
  const second = sessions[1];
  second.socket.send(Buffer.from("Second session output\r\n", "utf8"));
  await expect(output).toContainText("Second session output");

  await page.getByRole("button", { name: "Reset session" }).click();
  await expect.poll(() => sessions.length).toBe(3);
  await expect.poll(() => second.closed).toBe(true);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  await expect(output).not.toContainText("Second session output");
  await expect(
    page.getByRole("button", { name: "Insert command to read welcome.txt" }),
  ).toBeEnabled();
});

test("shows operator status as text and preserves its reason after a server disconnect", async ({
  page,
}) => {
  const sessions = await openPlayground(page);
  const session = sessions[0];
  const message = "Session limit reached. <b>Try again later.</b>";
  session.socket.send(JSON.stringify({ type: "status", message }));
  await expect(page.locator("#session-message")).toHaveText(message);
  await expect(page.locator("#session-message b")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start new session" })).toBeHidden();

  await session.socket.close({ code: 1013, reason: "Session limit" });
  await expect(page.locator("#connection-status")).toHaveText("Disconnected");
  await expect(page.locator("#session-message")).toHaveText(message);
  await expect(page.getByRole("button", { name: "Start new session" })).toBeEnabled();
});

test("server-approved metadata opens a terminal even when the browser clock is ahead of expiry", async ({
  page,
}) => {
  await page.clock.setFixedTime(new Date("2100-01-01T00:00:00Z"));
  const sessions = await openPlayground(page);
  expect(sessions).toHaveLength(1);
  expect(new URL(sessions[0].socket.url()).searchParams.get("revision")).toBe(metadata.revision);
});

for (const [status, title] of [
  [404, "This playground wasn't found."],
  [410, "This playground has expired."],
] as const) {
  test(`unavailable metadata (${status}) never opens a terminal connection`, async ({ page }) => {
    await page.route(`**${metadataPath}`, (route) =>
      route.fulfill({ status, json: { ...metadata, expires_at: "2000-01-01T00:00:00Z" } }),
    );
    const sessions = await mockTerminal(page);
    await page.goto(previewPath);
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
    await expect(page.getByRole("button", { name: "Try again" })).toBeHidden();
    await expect(page.locator(".xterm")).toHaveCount(0);
    expect(sessions).toHaveLength(0);
  });
}

test("unavailable metadata can be retried before opening any terminal connection", async ({
  page,
}) => {
  let requests = 0;
  await page.route(`**${metadataPath}`, (route) => {
    requests += 1;
    return route.fulfill({ status: requests === 1 ? 503 : 200, json: metadata });
  });
  const sessions = await mockTerminal(page);
  await page.goto(previewPath);
  await expect(page.getByRole("heading", { name: "The playground is unavailable." })).toBeVisible();
  await expect(page.locator(".xterm")).toHaveCount(0);
  expect(sessions).toHaveLength(0);

  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  await expect.poll(() => sessions.length).toBe(1);
  expect(requests).toBe(2);
});

test("a successful HTML response is reported as invalid metadata", async ({ page }) => {
  await page.route(`**${metadataPath}`, (route) =>
    route.fulfill({ contentType: "text/html", body: "<html>Proxy error</html>" }),
  );
  const sessions = await mockTerminal(page);
  await page.goto(previewPath);
  await expect(page.getByRole("alert")).toContainText("This build returned invalid metadata.");
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  expect(sessions).toHaveLength(0);
});
