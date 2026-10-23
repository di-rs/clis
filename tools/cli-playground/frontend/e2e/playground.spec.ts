import { argument, catalog } from "../testing/catalog";
import { openExplorer, openSampleFiles } from "./explorer";
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

type ClientMessage = {
  type: string;
  credential?: string;
  data?: string;
  epoch?: number;
  cols?: number;
  rows?: number;
  resume_after?: number;
  nonce?: number;
};

interface Session {
  socket: WebSocketRoute;
  messages: ClientMessage[];
  closed: boolean;
  deleted: boolean;
  sequence: number;
}

async function mockTerminal(
  page: Page,
  identifier = environmentId,
  names = ["catr"],
): Promise<Session[]> {
  await page.route(`**/api/environments/${identifier}/revisions/*/catalog`, (route) => {
    const revision = route.request().url().split("/").at(-2)!;
    return route.fulfill({ json: catalog(revision.split("-")[0], names) });
  });
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
      const session: Session = { socket, messages: [], closed: false, deleted: false, sequence: 0 };
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
        if (data.type === "attach") {
          session.sequence = data.resume_after ?? 0;
          socket.send(JSON.stringify({ type: "attached", epoch: 1 }));
          socket.send(
            JSON.stringify({ type: "replay_complete", epoch: 1, sequence: session.sequence }),
          );
        } else if (data.type === "resize") {
          socket.send(
            JSON.stringify({ type: "resized", epoch: 1, cols: data.cols, rows: data.rows }),
          );
        } else if (data.type === "heartbeat") socket.send(JSON.stringify(data));
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

function output(session: Session, data: Buffer) {
  const header = Buffer.alloc(8);
  header.writeBigUInt64BE(BigInt(++session.sequence));
  session.socket.send(Buffer.concat([header, data]));
}

function inputs(session: Session): string[] {
  return session.messages.flatMap((message) => (message.type === "input" ? [message.data!] : []));
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
  await expect(page).toHaveTitle("catr / cli-playground");
  await expect(page.locator("#meta-pr")).toHaveText("PR #42 ↗");
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
        (message) =>
          message.type === "resize" && (message.cols ?? 0) > 0 && (message.rows ?? 0) > 0,
      ),
    )
    .toBe(true);

  output(session, Buffer.from("Native terminal output\r\n", "utf8"));
  await expect(page.locator(".xterm-accessibility-tree")).toContainText("Native terminal output");
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true);

  await openExplorer(page);
  await page.getByRole("button", { name: "Insert catr", exact: true }).click();
  await expect.poll(() => inputs(session)).toEqual(["/opt/artifact/catr "]);
  await page.keyboard.press("Enter");
  await expect.poll(() => inputs(session)).toEqual(["/opt/artifact/catr ", "\r"]);

  await openSampleFiles(page);
  await page.getByRole("button", { name: "Insert command to read welcome.txt" }).click();
  await expect
    .poll(() => inputs(session))
    .toEqual(["/opt/artifact/catr ", "\r", "cat welcome.txt"]);

  await openExplorer(page);
  await page.locator(".shell-details > summary").click();
  await page.getByRole("button", { name: "Insert cat welcome.txt | wc -l: Pipe output" }).click();
  await expect
    .poll(() => inputs(session))
    .toEqual(["/opt/artifact/catr ", "\r", "cat welcome.txt", "cat welcome.txt | wc -l"]);

  await page.keyboard.type(" && printf 'manual'");
  await expect
    .poll(() => inputs(session).join(""))
    .toBe("/opt/artifact/catr \rcat welcome.txtcat welcome.txt | wc -l && printf 'manual'");
});

for (const count of [3, 5, 10]) {
  test(`selects among ${count} CLIs within the same PR terminal and URL`, async ({ page }) => {
    const clis = ["catr", ...Array.from({ length: count - 1 }, (_, index) => `tool${index + 1}`)];
    const target = clis[count - 1];
    await page.route(`**${metadataPath}`, (route) =>
      route.fulfill({ json: { ...metadata, clis } }),
    );
    const sessions = await mockTerminal(page, environmentId, clis);
    await page.goto(previewPath);
    await expect(page.locator("#connection-status")).toHaveText("Connected");
    const session = sessions[0];
    output(session, Buffer.from("Existing workspace output\r\n", "utf8"));
    const screen = page.locator(".xterm-accessibility-tree");
    await expect(screen).toContainText("Existing workspace output");
    const terminal = await page.locator("#terminal").elementHandle();
    await page.locator(".xterm-helper-textarea").focus();
    await page.keyboard.type("partially typed");
    await openExplorer(page);
    const picker = page.getByRole("combobox", { name: "CLI", exact: true });
    await picker.click();
    await expect(page.getByRole("option")).toHaveCount(count);
    for (const name of [target, "catr", target]) {
      await picker.fill(name);
      await picker.press("ArrowDown");
      await picker.press("Enter");
      await expect(picker).toHaveValue(name);
      await page.getByRole("textbox", { name: "Search commands and flags" }).fill("number");
      await page.getByRole("button", { name: "Changes", exact: false }).click();
      await page.getByRole("button", { name: "All", exact: true }).click();
      await page.getByRole("textbox", { name: "Search commands and flags" }).clear();
      const close = page.getByRole("button", { name: "Close explorer ×" });
      if (await close.isVisible()) {
        await close.click();
        await openExplorer(page);
        await picker.press("Escape");
      }
    }
    expect(await terminal!.evaluate((node) => node.isConnected)).toBe(true);
    await expect(page).toHaveURL(previewPath);
    await expect(page).toHaveTitle(`${target} / cli-playground`);
    await expect(screen).toContainText("Existing workspace output");
    await expect(picker).toHaveValue(target);
    await page.getByRole("button", { name: `Insert ${target}`, exact: true }).click();
    await expect
      .poll(() => inputs(session).join(""))
      .toBe(`partially typed/opt/artifact/${target} `);
    expect(session.closed).toBe(false);
    expect(sessions).toHaveLength(1);
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
      .toBe(true);
  });
}

test("pastes a large Unicode document through bounded messages in order", async ({ page }) => {
  const [session] = await openPlayground(page);
  output(session, Buffer.from("\x1b[?2004hReady to paste", "utf8"));
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

for (const action of ["Reset session"]) {
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
    await page.route(`**/api/environments/${environmentId}/revisions/*/catalog`, (route) =>
      route.fulfill({ json: catalog("b".repeat(40), ["echor", "wcr"]) }),
    );
    await page.getByRole("button", { name: action, exact: true }).click();
    await expect(page.locator("#meta-sha")).toHaveText("bbbbbbb");
    await openExplorer(page);
    await page.getByRole("combobox", { name: "CLI", exact: true }).press("Escape");
    await expect(page.getByRole("combobox", { name: "CLI", exact: true })).toHaveValue("echor");
    await expect(page.getByRole("button", { name: "Insert echor", exact: true })).toBeEnabled();
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
  await page.getByRole("button", { name: "Reset session" }).click();
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
  output(first, Buffer.from("My work is still here\r\n"));
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
  await openExplorer(page);
  await page.getByRole("button", { name: "Insert catr", exact: true }).click();
  await expect.poll(() => inputs(sessions[0])).toEqual(["/opt/artifact/catr "]);
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

test("disconnect and reconnect retain output while reset starts a fresh terminal", async ({
  page,
}) => {
  const sessions = await openPlayground(page);
  const first = sessions[0];
  const screen = page.locator(".xterm-accessibility-tree");
  output(first, Buffer.from("First session output\r\n", "utf8"));
  await expect(screen).toContainText("First session output");

  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  await expect(page.locator("#connection-status")).toHaveText("Disconnected");
  await expect.poll(() => first.closed).toBe(true);
  await expect(screen).toContainText("First session output");
  await expect(page.getByRole("button", { name: "Disconnect", exact: true })).toBeDisabled();
  await openSampleFiles(page);
  await expect(
    page.getByRole("button", { name: "Insert command to read welcome.txt" }),
  ).toBeDisabled();
  const close = page.getByRole("button", { name: "Close explorer ×" });
  if (await close.isVisible()) await close.click();
  await page.locator(".xterm-helper-textarea").focus();
  await page.keyboard.type("ignored");
  expect(inputs(first)).toEqual([]);

  await page.getByRole("button", { name: "Reconnect", exact: true }).click();
  await expect.poll(() => sessions.length).toBe(2);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  await expect(screen).toContainText("First session output");
  const second = sessions[1];
  output(second, Buffer.from("Second session output\r\n", "utf8"));
  await expect(screen).toContainText("Second session output");

  await page.getByRole("button", { name: "Reset session" }).click();
  await expect.poll(() => sessions.length).toBe(3);
  await expect.poll(() => second.closed).toBe(true);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  await expect(screen).not.toContainText("Second session output");
  await openSampleFiles(page);
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
  session.socket.send(JSON.stringify({ type: "status", reason: "output_limit", message }));
  await expect(page.locator("#session-message")).toHaveText(message);
  await expect(page.locator("#session-message b")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start new session" })).toBeEnabled();

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

test("explorer exposes change details and inserts flags without values or execution", async ({
  page,
}) => {
  const [session] = await openPlayground(page);
  await openExplorer(page);
  await expect(page.locator('[data-change="changed"] .badge')).toHaveText("~ Changed");
  await expect(page.locator('[data-change="added"] .badge')).toHaveText("+ Added");
  await page.locator('[data-change="changed"] summary').click();
  await expect(page.locator('[data-change="changed"] del')).toHaveText("Number output");
  await expect(page.locator('[data-change="changed"] ins')).toHaveText("Number each line");
  await page.locator(".removed-section > summary").click();
  await expect(page.locator(".removed-section")).toContainText("--old");
  await expect(page.locator(".removed-section button:not(:disabled)")).toHaveCount(0);
  await page.getByRole("button", { name: "Insert --format", exact: true }).click();
  await expect.poll(() => inputs(session)).toEqual([" --format "]);
  await expect(page.getByRole("textbox", { name: "Terminal input" })).toBeFocused();
  await page.keyboard.type("json");
  await expect.poll(() => inputs(session).join("")).toBe(" --format json");
});

test("long references keep search pinned and sample files reachable without disturbing the terminal", async ({
  page,
}) => {
  const sessions = await mockTerminal(page);
  await page.route(`**${metadataPath}`, (route) => route.fulfill({ json: metadata }));
  const reference = catalog(metadata.sha);
  reference.clis[0].head.command.args.push(
    ...Array.from({ length: 30 }, (_, index) =>
      argument({
        id: `option-${index}`,
        long: `option-${index}`,
        short: null,
        help: `Configure output option ${index}`,
      }),
    ),
  );
  await page.route("**/catalog", (route) => route.fulfill({ json: reference }));
  await page.goto(previewPath);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  const terminal = await page.locator("#terminal").elementHandle();
  await openExplorer(page);
  const picker = page.getByRole("combobox", { name: "CLI", exact: true });
  await expect(picker).toHaveAttribute("aria-expanded", "false");
  const referenceArea = page.locator(".explorer-scroll");
  await referenceArea.hover();
  await page.mouse.wheel(0, 1800);
  await expect.poll(() => referenceArea.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  await expect(picker).toBeInViewport();
  const search = page.getByRole("textbox", { name: "Search commands and flags" });
  await expect(search).toBeInViewport();
  await page.locator(".workspace-details > summary").scrollIntoViewIfNeeded();
  await expect(page.locator(".workspace-details > summary")).toBeInViewport();
  await expect(search).toBeInViewport();
  await search.fill("option-24");
  await expect(page.getByRole("button", { name: "Insert --option-24", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear command search" }).click();
  await expect(search).toBeFocused();
  await expect(search).toHaveValue("");
  expect(await terminal!.evaluate((node) => node.isConnected)).toBe(true);
  expect(sessions).toHaveLength(1);
  expect(inputs(sessions[0])).toEqual([]);
});

test("catalog remains on the active revision through build polling", async ({ page }) => {
  await page.clock.install();
  let latest = { ...metadata };
  await page.route(`**${metadataPath}`, (route) => route.fulfill({ json: latest }));
  const sessions = await mockTerminal(page);
  const requests: string[] = [];
  await page.route(`**/api/environments/${environmentId}/revisions/*/catalog`, (route) => {
    const revision = route.request().url().split("/").at(-2)!;
    requests.push(revision);
    const reference = catalog(revision, latest.clis);
    reference.clis[0].head.command.about =
      revision === metadata.sha ? "Original reference" : "Updated reference";
    return route.fulfill({ json: reference });
  });
  await page.goto(previewPath);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  await openExplorer(page);
  await expect(page.locator(".command-about")).toHaveText("Original reference");
  latest = { ...metadata, sha: "b".repeat(40), revision: "b".repeat(40) };
  await page.clock.fastForward(30_000);
  expect(requests).toEqual([metadata.sha]);
  await expect(page.locator(".command-about")).toHaveText("Original reference");
  const close = page.getByRole("button", { name: "Close explorer ×" });
  if (await close.isVisible()) await close.click();
  await page.getByRole("button", { name: "Switch to latest build" }).click();
  await expect.poll(() => sessions.length).toBe(2);
  await openExplorer(page);
  await expect(page.locator(".command-about")).toHaveText("Updated reference");
  expect(requests).toEqual([metadata.sha, "b".repeat(40)]);
});

test("legacy previews and unavailable comparisons remain usable without speculative badges", async ({
  page,
}) => {
  const sessions = await mockTerminal(page);
  await page.route(`**${metadataPath}`, (route) => route.fulfill({ json: metadata }));
  await page.route("**/catalog", (route) => route.fulfill({ json: null }));
  await page.goto(previewPath);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  await openExplorer(page);
  await expect(page.locator(".catalog-status")).toContainText("Command reference unavailable");
  const reference = catalog(metadata.sha);
  reference.clis[0].base = null;
  reference.clis[0].comparison = "unavailable";
  await page.route("**/catalog", (route) => route.fulfill({ json: reference }));
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.locator(".catalog-status")).toHaveText("Comparison unavailable");
  await expect(page.locator(".badge")).toHaveCount(0);
  expect(sessions).toHaveLength(1);
});

test("metadata is plain text and reduced motion preserves the same terminal", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const sessions = await mockTerminal(page);
  await page.route(`**${metadataPath}`, (route) => route.fulfill({ json: metadata }));
  const reference = catalog(metadata.sha);
  reference.clis[0].head.command.about = '<img src=x onerror="alert(1)">';
  await page.route("**/catalog", (route) => route.fulfill({ json: reference }));
  await page.goto(previewPath);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  const terminal = await page.locator("#terminal").elementHandle();
  await openExplorer(page);
  await expect(page.locator(".command-about")).toHaveText(reference.clis[0].head.command.about);
  await expect(page.locator(".command-about img")).toHaveCount(0);
  expect(
    await page.locator(".sidebar").evaluate((node) => getComputedStyle(node).transitionDuration),
  ).toBe("0s");
  const search = page.getByRole("textbox", { name: "Search commands and flags" });
  await search.fill("no matching command");
  await expect(page.getByText("No matching commands or flags.")).toBeVisible();
  expect(await terminal!.evaluate((node) => node.isConnected)).toBe(true);
  expect(sessions).toHaveLength(1);
});
