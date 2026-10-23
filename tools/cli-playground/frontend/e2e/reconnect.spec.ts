import { openSampleFiles } from "./explorer";
import { Buffer } from "node:buffer";
import { expect, test, type Page, type APIRequestContext } from "@playwright/test";
import { observeTerminalResize, resizeTerminalViewport } from "./resize";

test.describe.configure({ mode: "serial" });
interface Sandbox {
  id: string;
  page_session_ids: string[];
  epoch: number;
  attached: boolean;
  last_input_at: string;
  writes: string[];
  sizes: number[][];
}
interface Trace {
  sockets: WebSocket[];
  inputs: { data: string; source: string; epoch: number }[];
  dropAck: boolean;
  dropResize: boolean;
  duplicateAndClose: boolean;
  cursors: number[];
  credential: string;
}
async function state(
  request: APIRequestContext,
): Promise<{ environment: string; sandboxes: Sandbox[] }> {
  return (await request.get("/__test__/state")).json();
}
async function open(page: Page, request: APIRequestContext) {
  await page.addInitScript(() => {
    const trace = {
      sockets: [] as WebSocket[],
      inputs: [] as object[],
      dropAck: false,
      dropResize: false,
      duplicateAndClose: false,
      cursors: [] as number[],
      credential: "",
    };
    Object.assign(window, { terminalTrace: trace });
    const Native = window.WebSocket;
    window.WebSocket = class extends Native {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols);
        trace.sockets.push(this);
        this.addEventListener("message", (event) => {
          if (trace.duplicateAndClose && event.data instanceof ArrayBuffer) {
            trace.duplicateAndClose = false;
            event.stopImmediatePropagation();
            // Two deliveries in the same turn precede xterm's asynchronous write
            // completion. Close now, retaining those pending same-sandbox writes.
            this.dispatchEvent(new MessageEvent("message", { data: event.data }));
            this.dispatchEvent(new MessageEvent("message", { data: event.data }));
            this.dispatchEvent(new CloseEvent("close"));
            this.close();
            return;
          }
          if (
            trace.dropResize &&
            typeof event.data === "string" &&
            JSON.parse(event.data).type === "resized"
          ) {
            event.stopImmediatePropagation();
            this.close();
          }
        });
      }
      send(value: string | Blob | BufferSource) {
        if (typeof value === "string") {
          const frame = JSON.parse(value);
          if (frame.type === "auth") trace.credential = frame.credential;
          if (frame.type === "input") trace.inputs.push(frame);
          if (frame.type === "attach") trace.cursors.push(frame.resume_after);
          if (trace.dropAck && frame.type === "ack") return;
        }
        super.send(value);
      }
    };
  });
  const before = await state(request);
  await page.goto(`/p/${before.environment}`);
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  return currentSandbox(page, request);
}
async function currentSandbox(page: Page, request: APIRequestContext) {
  const sessionId = await page.evaluate(() => {
    const socket = (window as unknown as { terminalTrace: Trace }).terminalTrace.sockets.at(-1)!;
    return new URL(socket.url).searchParams.get("session_id")!;
  });
  return (await state(request)).sandboxes.find((s) => s.page_session_ids.includes(sessionId))!;
}
async function output(request: APIRequestContext, id: string, chunks: Buffer[]) {
  // Keep base64 plus JSON below the application's 16 KiB request-body limit.
  // Splitting large chunks preserves the byte stream and the retention pressure.
  for (const chunk of chunks) {
    for (let offset = 0; offset < chunk.length; offset += 8192) {
      const response = await request.post("/__test__/output", {
        data: { id, chunks: [chunk.subarray(offset, offset + 8192).toString("base64")] },
      });
      expect(response.ok(), await response.text()).toBe(true);
    }
  }
}

async function inputs(page: Page) {
  return page.evaluate(() => (window as unknown as { terminalTrace: Trace }).terminalTrace.inputs);
}
async function disconnect(page: Page) {
  await page.evaluate(() =>
    (window as unknown as { terminalTrace: Trace }).terminalTrace.sockets.at(-1)!.close(),
  );
  await expect(page.locator("#connection-status")).toHaveText("Disconnected");
}

test("viewport input waits for the current resize acknowledgement instead of old Connected or stale geometry", async ({
  page,
  request,
}) => {
  let hold = false;
  let held: { release(): void; wrong(): void } | undefined;
  await page.routeWebSocket("**/ws/**", (socket) => {
    const server = socket.connectToServer();
    server.onMessage((message) => {
      if (hold && typeof message === "string") {
        const frame = JSON.parse(message);
        if (frame.type === "resized") {
          held = {
            release: () => {
              socket.send(message);
              held = undefined;
            },
            wrong: () => {
              socket.send(JSON.stringify({ ...frame, epoch: frame.epoch - 1 }));
              socket.send(JSON.stringify({ ...frame, cols: frame.cols + 1 }));
            },
          };
          return;
        }
      }
      socket.send(message);
    });
  });
  await observeTerminalResize(page);
  const first = await open(page, request);
  await disconnect(page);
  await page.getByRole("button", { name: "Reconnect", exact: true }).click();
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  expect((await currentSandbox(page, request)).id).toBe(first.id);
  await page.clock.install();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  hold = true;
  let finished = false;
  const resized = resizeTerminalViewport(page, { width: 900, height: 700 }).then(() => {
    finished = true;
  });
  const settled = resized.catch(() => {});
  try {
    await page.waitForFunction(() => window.innerWidth === 900);
    await expect(page.locator("#connection-status")).toHaveText("Connected");
    expect(finished).toBe(false);
    await page.clock.runFor(100);
    await expect.poll(() => !!held).toBe(true);
    await expect(page.locator("#connection-status")).toHaveText("Synchronizing");
    held!.wrong();
    await page.clock.runFor(100);
    await expect(page.locator("#connection-status")).toHaveText("Synchronizing");
    expect(finished).toBe(false);
    await page.locator(".xterm-helper-textarea").pressSequentially("discarded");
    expect((await inputs(page)).map((i) => i.data).join("")).toBe("");
    hold = false;
    held!.release();
    await page.clock.resume();
    await resized;
    await page.locator(".xterm-helper-textarea").pressSequentially("after-resize");
    await expect
      .poll(async () =>
        Buffer.concat(
          (await state(request)).sandboxes
            .find((s) => s.id === first.id)!
            .writes.map((x) => Buffer.from(x, "base64")),
        ).toString(),
      )
      .toBe("after-resize");
  } finally {
    hold = false;
    held?.release();
    await page.clock.resume();
    await settled;
  }
});

test("real product/API/supervisor reconnect preserves one PTY and Unicode screen, loses no applied cursor, never replays input, and reload is fresh", async ({
  page,
  request,
}) => {
  const first = await open(page, request);
  const screen = page.locator(".xterm-accessibility-tree");
  await page.locator(".xterm-helper-textarea").focus();
  await page.keyboard.type("one");
  await expect(screen).toContainText("one");
  await page.evaluate(
    () => ((window as unknown as { terminalTrace: Trace }).terminalTrace.dropAck = true),
  );
  const unicode = Buffer.from("\r\nunique-漢🙂-marker\r\n");
  await output(request, first.id, [unicode.subarray(0, 11), unicode.subarray(11)]);
  await expect(screen).toContainText("unique-漢🙂-marker");
  const old = (await state(request)).sandboxes.find((s) => s.id === first.id)!;
  await disconnect(page);
  await page.setViewportSize({ width: 1100, height: 750 });
  await page.locator(".xterm-helper-textarea").focus();
  await page.keyboard.type("discarded");
  expect((await inputs(page)).map((i) => i.data).join("")).toBe("one");
  expect((await state(request)).sandboxes.find((s) => s.id === first.id)!.sizes).toEqual(old.sizes);
  await page.evaluate(
    () => ((window as unknown as { terminalTrace: Trace }).terminalTrace.dropAck = false),
  );
  await page.getByRole("button", { name: "Reconnect", exact: true }).click();
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  expect((await screen.innerText()).split("unique-漢🙂-marker")).toHaveLength(2);
  const current = (await state(request)).sandboxes.find((s) => s.id === first.id)!;
  expect(current.epoch).toBe(first.epoch + 1);
  expect(current.writes).toEqual(old.writes);
  await page.locator(".xterm-helper-textarea").focus();
  await page.keyboard.type("two");
  await expect
    .poll(async () =>
      Buffer.concat(
        (await state(request)).sandboxes
          .find((s) => s.id === first.id)!
          .writes.map((x) => Buffer.from(x, "base64")),
      ).toString(),
    )
    .toBe("onetwo");
  await page.reload();
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  expect((await currentSandbox(page, request)).id).not.toBe(first.id);
  await expect(screen).not.toContainText("unique-漢🙂-marker");
});

test("shipped xterm marks keyboard, clipboard, example and asynchronous composition as user, with parser replies during replay", async ({
  page,
  request,
}) => {
  const first = await open(page, request);
  const textarea = page.locator(".xterm-helper-textarea");
  await textarea.focus();
  await page.keyboard.type("K");
  await textarea.evaluate((node) => {
    const clipboardData = new DataTransfer();
    clipboardData.setData("text/plain", "paste");
    node.dispatchEvent(
      new ClipboardEvent("paste", { clipboardData, bubbles: true, cancelable: true }),
    );
  });
  await openSampleFiles(page);
  await page.getByRole("button", { name: "Insert command to read welcome.txt" }).click();
  await expect
    .poll(async () =>
      (await inputs(page))
        .filter((i) => i.source === "user")
        .map((i) => i.data)
        .join(""),
    )
    .toContain("paste");
  const before = (await state(request)).sandboxes.find((s) => s.id === first.id)!;
  await disconnect(page);
  await output(request, first.id, [Buffer.from("\x1b[6n")]);
  await page.getByRole("button", { name: "Reconnect", exact: true }).click();
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  await expect
    .poll(async () => (await inputs(page)).filter((i) => i.source === "terminal").length)
    .toBe(1);
  expect((await state(request)).sandboxes.find((s) => s.id === first.id)!.last_input_at).toBe(
    before.last_input_at,
  );
  await textarea.focus();
  await textarea.evaluate((node) => {
    const area = node as HTMLTextAreaElement;
    area.value = "";
    area.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    area.value = "漢";
    area.dispatchEvent(new CompositionEvent("compositionupdate", { data: "漢", bubbles: true }));
  });
  await output(request, first.id, [Buffer.from("\x1b[6n")]);
  await expect
    .poll(async () => (await inputs(page)).filter((i) => i.source === "terminal").length)
    .toBe(2);
  await textarea.evaluate((node) =>
    node.dispatchEvent(new CompositionEvent("compositionend", { data: "漢", bubbles: true })),
  );
  await expect
    .poll(
      async () => (await inputs(page)).filter((i) => i.source === "user" && i.data === "漢").length,
    )
    .toBe(1);
  const captured = await inputs(page);
  expect(captured.find((i) => i.data === "K")?.source).toBe("user");
  expect(captured.find((i) => i.data === "paste")?.source).toBe("user");
  expect(captured.find((i) => i.data.includes("welcome.txt"))?.source).toBe("user");
  expect(
    captured
      .filter((i) => i.source === "terminal")
      .every((i) => /^\[\d+;\d+R$/.test(i.data.slice(1))),
  ).toBe(true);
});

test("real browser stops a missing heartbeat path after thirty seconds", async ({
  page,
  request,
}) => {
  await page.clock.install();
  await open(page, request);
  await page.evaluate(() => {
    const trace = (window as unknown as { terminalTrace: Trace }).terminalTrace;
    const socket = trace.sockets.at(-1)!;
    const send = socket.send.bind(socket);
    socket.send = (value) => {
      if (typeof value === "string" && JSON.parse(value).type === "heartbeat") return;
      send(value);
    };
  });
  await page.clock.runFor(30000);
  await expect(page.locator("#connection-status")).toHaveText("Disconnected");
  await expect(page.getByRole("button", { name: "Reconnect", exact: true })).toBeEnabled();
});

test("retention overflow offers a fresh session instead of restoring a partial screen", async ({
  page,
  request,
}) => {
  const first = await open(page, request);
  await disconnect(page);
  await output(
    request,
    first.id,
    Array.from({ length: 65 }, () => Buffer.alloc(16384, 120)),
  );
  await page.getByRole("button", { name: "Reconnect", exact: true }).click();
  await expect(page.locator("#session-message")).toContainText("stream_gap");
  await expect(page.getByRole("button", { name: "Reconnect", exact: true })).toBeHidden();
  await page.getByRole("button", { name: "Start new session", exact: true }).click();
  await expect(page.locator("#connection-status")).toHaveText("Connected");
});

test("unacknowledged browser output is bounded while PTY drain continues to a stream gap", async ({
  page,
  request,
}) => {
  const first = await open(page, request);
  await page.evaluate(
    () => ((window as unknown as { terminalTrace: Trace }).terminalTrace.dropAck = true),
  );
  await output(
    request,
    first.id,
    Array.from({ length: 80 }, () => Buffer.alloc(16384, 120)),
  );
  await expect(page.locator("#session-message")).toContainText("stream_gap");
  await expect(page.getByRole("button", { name: "Start new session", exact: true })).toBeEnabled();
});

test("lost resize acknowledgement replays later output at the retained PTY geometry", async ({
  page,
  request,
}) => {
  const first = await open(page, request);
  await page.evaluate(
    () => ((window as unknown as { terminalTrace: Trace }).terminalTrace.dropResize = true),
  );
  await page.setViewportSize({ width: 480, height: 700 });
  await expect(page.locator("#connection-status")).toHaveText("Disconnected");
  const resized = (await state(request)).sandboxes.find((s) => s.id === first.id)!;
  const columns = resized.sizes.at(-1)![0];
  await output(request, first.id, [Buffer.from("\x1b[1;1H\x1b[999C\x1b[6n")]);
  await page.evaluate(
    () => ((window as unknown as { terminalTrace: Trace }).terminalTrace.dropResize = false),
  );
  await page.getByRole("button", { name: "Reconnect", exact: true }).click();
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  await expect
    .poll(async () =>
      (await inputs(page)).filter((i) => i.source === "terminal").map((i) => i.data),
    )
    .toEqual([`\x1b[1;${columns}R`]);
});

test("duplicate frames and pending xterm writes survive transport loss without duplicate display or stale close", async ({
  page,
  request,
}) => {
  const first = await open(page, request);
  await page.evaluate(
    () => ((window as unknown as { terminalTrace: Trace }).terminalTrace.duplicateAndClose = true),
  );
  await output(request, first.id, [Buffer.from("\r\npending-once-漢🙂\r\n")]);
  await expect(page.locator("#connection-status")).toHaveText("Disconnected");
  await page.getByRole("button", { name: "Reconnect", exact: true }).click();
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  const screen = page.locator(".xterm-accessibility-tree");
  await expect(screen).toContainText("pending-once-漢🙂");
  expect((await screen.innerText()).split("pending-once-漢🙂")).toHaveLength(2);
  expect(
    await page.evaluate(
      () => (window as unknown as { terminalTrace: Trace }).terminalTrace.cursors,
    ),
  ).toEqual([0, 2]);
  await page.evaluate(() => {
    const trace = (window as unknown as { terminalTrace: Trace }).terminalTrace;
    trace.sockets[0].dispatchEvent(new CloseEvent("close"));
  });
  await page.locator(".xterm-helper-textarea").focus();
  await page.keyboard.type("live");
  await expect(screen).toContainText("live");
  expect((await state(request)).sandboxes.find((s) => s.id === first.id)!.epoch).toBe(
    first.epoch + 1,
  );
});

test("a viewport above the row limit settles at 300 rows and accepts input", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1200, height: 10000 });
  const first = await open(page, request);
  const before = (await state(request)).sandboxes.find((s) => s.id === first.id)!;
  expect(before.sizes.at(-1)![1]).toBe(300);
  await page.locator(".xterm-helper-textarea").focus();
  await page.keyboard.type("bounded geometry usable");
  await expect(page.locator(".xterm-accessibility-tree")).toContainText("bounded geometry usable");
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  const after = (await state(request)).sandboxes.find((s) => s.id === first.id)!;
  expect(after.sizes.length).toBeLessThanOrEqual(before.sizes.length + 1);
});

test("a delayed obsolete browser attach cannot displace the reconnected terminal", async ({
  page,
  request,
}) => {
  const first = await open(page, request);
  await page.evaluate(async () => {
    const trace = (window as unknown as { terminalTrace: Trace }).terminalTrace;
    const old = new WebSocket(trace.sockets[0].url);
    await new Promise<void>((resolve, reject) => {
      old.addEventListener("open", () =>
        old.send(JSON.stringify({ type: "auth", credential: trace.credential })),
      );
      old.addEventListener("message", (event) => {
        if (typeof event.data === "string" && JSON.parse(event.data).type === "authenticated")
          resolve();
      });
      old.addEventListener("error", reject);
    });
    trace.sockets[0].close();
  });
  await expect(page.locator("#connection-status")).toHaveText("Disconnected");
  await page.getByRole("button", { name: "Reconnect", exact: true }).click();
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  const healthy = (await state(request)).sandboxes.find((s) => s.id === first.id)!;
  await page.evaluate(async () => {
    const trace = (window as unknown as { terminalTrace: Trace }).terminalTrace;
    const old = trace.sockets[1];
    await new Promise<void>((resolve) => {
      old.addEventListener("close", () => resolve());
      old.send(JSON.stringify({ type: "attach", resume_after: 0, cols: 80, rows: 24 }));
    });
  });
  await page.locator(".xterm-helper-textarea").focus();
  await page.keyboard.type("successor remains usable");
  await expect(page.locator(".xterm-accessibility-tree")).toContainText("successor remains usable");
  await expect(page.locator("#connection-status")).toHaveText("Connected");
  expect((await state(request)).sandboxes.find((s) => s.id === first.id)!.epoch).toBe(
    healthy.epoch,
  );
});
