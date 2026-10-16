import { describe, expect, it, vi } from "vite-plus/test";
import { createSession, type SessionState, type TerminalPort } from "./session";

class TestTerminal implements TerminalPort {
  cols = 80;
  rows = 24;
  options = { disableStdin: false };
  output: number[] = [];
  callbacks: (() => void)[] = [];
  delayed = false;
  resets = 0;
  input?: (value: { data: string; source: "user" | "terminal" }) => void;
  write(data: Uint8Array, callback?: () => void) {
    this.output.push(...data);
    if (callback) {
      if (this.delayed) this.callbacks.push(callback);
      else callback();
    }
  }
  drain() {
    while (this.callbacks.length) this.callbacks.shift()!();
  }
  paste(data: string) {
    this.input?.({ data, source: "user" });
  }
  focus() {}
  reset() {
    this.resets++;
    this.output = [];
  }
}
class TestSocket extends EventTarget {
  readyState = 0;
  bufferedAmount = 0;
  binaryType = "blob";
  sent: Record<string, unknown>[] = [];
  close() {
    this.readyState = 3;
  }
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  open() {
    this.readyState = 1;
    this.dispatchEvent(new Event("open"));
    this.control({
      type: "authenticated",
      state: "ready",
      absolute_deadline: "2099-01-01T00:00:00Z",
    });
  }
  control(data: object) {
    this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(data) }));
  }
  frame(sequence: number, data: string) {
    const payload = new TextEncoder().encode(data);
    const wire = new Uint8Array(payload.length + 8);
    new DataView(wire.buffer).setBigUint64(0, BigInt(sequence));
    wire.set(payload, 8);
    this.dispatchEvent(new MessageEvent("message", { data: wire.buffer }));
  }
  end() {
    this.readyState = 3;
    this.dispatchEvent(new Event("close"));
  }
}
function setup() {
  const terminal = new TestTerminal();
  const sockets: TestSocket[] = [];
  const states: SessionState[] = [];
  let prepared = 0,
    stopped = 0,
    enabled = false,
    applied = 0;
  let desired = { cols: 80, rows: 24 };
  const session = createSession({
    terminal,
    input: {
      onInput(listener) {
        terminal.input = listener;
        return {
          dispose: () => {
            terminal.input = undefined;
          },
        };
      },
      setUserEnabled(value) {
        enabled = value;
      },
    },
    geometry: {
      measure: () => desired,
      apply(size) {
        terminal.cols = size.cols;
        terminal.rows = size.rows;
        applied++;
      },
    },
    prepare: async () => {
      prepared++;
      return {
        credential: "page-secret",
        stop: async () => {
          stopped++;
        },
        createSocket: () => {
          const socket = new TestSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      };
    },
    onState: (state) => states.push(state),
  });
  const socket = () => sockets.at(-1)!;
  async function start() {
    session.startFresh();
    await Promise.resolve();
    socket().open();
    socket().control({ type: "attached", epoch: 1 });
  }
  function ready(epoch = 1, sequence = 0) {
    socket().control({ type: "replay_complete", epoch, sequence });
    terminal.drain();
    socket().control({ type: "resized", epoch, ...desired });
  }
  return {
    session,
    terminal,
    sockets,
    socket,
    start,
    ready,
    states,
    resize: (cols: number, rows: number) => {
      desired = { cols, rows };
      session.syncGeometry();
    },
    prepared: () => prepared,
    stopped: () => stopped,
    enabled: () => enabled,
    applied: () => applied,
  };
}

describe("same-page reconnect", () => {
  it("drains pending writes, deduplicates queued frames and resumes last applied cursor without input replay", async () => {
    const t = setup();
    await t.start();
    t.ready();
    t.terminal.input?.({ data: "one", source: "user" });
    t.terminal.delayed = true;
    t.socket().frame(1, "🙂");
    t.socket().frame(1, "🙂");
    const old = t.socket();
    old.end();
    t.terminal.input?.({ data: "lost", source: "user" });
    t.session.resumeExisting();
    expect(t.sockets).toHaveLength(1);
    t.terminal.drain();
    await Promise.resolve();
    expect(t.prepared()).toBe(1);
    expect(t.stopped()).toBe(0);
    expect(t.terminal.resets).toBe(1);
    t.socket().open();
    expect(t.socket().sent.at(-1)).toEqual({ type: "attach", resume_after: 1, cols: 80, rows: 24 });
    expect(old.sent.filter((x) => x.type === "ack")).toEqual([]);
    t.socket().control({ type: "attached", epoch: 2 });
    t.socket().frame(1, "🙂");
    t.ready(2, 1);
    t.terminal.drain();
    expect(new TextDecoder().decode(new Uint8Array(t.terminal.output))).toBe("🙂");
    expect(t.socket().sent.filter((x) => x.type === "input")).toEqual([]);
    old.end();
    expect(t.states.at(-1)?.phase).toBe("connected");
    t.session.dispose();
  });
  it("freezes geometry until replay drains and permits parser replies while user input is gated", async () => {
    const t = setup();
    await t.start();
    t.terminal.input?.({ data: "key", source: "user" });
    t.terminal.input?.({ data: "\x1b[1;1R", source: "terminal" });
    expect(t.socket().sent.filter((x) => x.type === "input")).toEqual([
      { type: "input", epoch: 1, source: "terminal", data: "\x1b[1;1R" },
    ]);
    t.resize(120, 40);
    expect(t.applied()).toBe(0);
    expect(t.enabled()).toBe(false);
    t.socket().control({ type: "replay_complete", epoch: 1, sequence: 0 });
    expect(t.socket().sent.at(-1)).toEqual({ type: "resize", epoch: 1, cols: 120, rows: 40 });
    expect(t.enabled()).toBe(false);
    t.socket().control({ type: "resized", epoch: 1, cols: 120, rows: 40 });
    expect(t.applied()).toBe(1);
    expect(t.enabled()).toBe(true);
    t.session.dispose();
  });
  it("starts a fresh admission after draining old output and ignores stale frames", async () => {
    const t = setup();
    await t.start();
    t.ready();
    const old = t.socket();
    t.terminal.delayed = true;
    old.frame(1, "old");
    t.session.startFresh();
    t.terminal.drain();
    await Promise.resolve();
    old.frame(2, "stale");
    old.end();
    expect(t.prepared()).toBe(2);
    expect(t.stopped()).toBe(1);
    expect(t.terminal.output).toEqual([]);
    expect(t.terminal.resets).toBe(2);
    t.session.dispose();
  });
  it("uses browser heartbeat responses, ignores stale pongs, and bounds browser sends", async () => {
    vi.useFakeTimers();
    const t = setup();
    await t.start();
    t.ready();
    vi.advanceTimersByTime(10000);
    expect(t.socket().sent.at(-1)).toEqual({ type: "heartbeat", epoch: 1, nonce: 1 });
    t.socket().control({ type: "heartbeat", epoch: 99, nonce: 1 });
    vi.advanceTimersByTime(20000);
    expect(t.states.at(-1)?.phase).toBe("disconnected");
    expect(t.stopped()).toBe(0);
    t.session.resumeExisting();
    await Promise.resolve();
    t.socket().open();
    t.socket().control({ type: "attached", epoch: 2 });
    t.ready(2);
    t.socket().bufferedAmount = 65537;
    t.terminal.input?.({ data: "overloaded", source: "user" });
    expect(t.socket().readyState).toBe(3);
    expect(t.socket().sent.filter((x) => x.type === "input")).toEqual([]);
    t.session.dispose();
    vi.useRealTimers();
  });
  it.each(["a".repeat(17000), '\u0000\n\r\t"\\'.repeat(4000), "é漢🙂".repeat(8000)])(
    "preserves large input with source and epoch inside frame limit",
    async (text) => {
      const t = setup();
      await t.start();
      t.ready();
      t.terminal.input?.({ data: text, source: "user" });
      const messages = t.socket().sent.filter((x) => x.type === "input");
      expect(messages.map((x) => x.data).join("")).toBe(text);
      for (const message of messages)
        expect(new TextEncoder().encode(JSON.stringify(message)).length).toBeLessThanOrEqual(16384);
      t.session.dispose();
    },
  );
  it("offers a fresh session on a replay gap", async () => {
    const t = setup();
    await t.start();
    t.socket().control({
      type: "status",
      reason: "stream_gap",
      message: "Output history is unavailable. Start a new session.",
    });
    t.socket().end();
    expect(t.states.at(-1)).toMatchObject({ canReconnect: false, reason: "stream_gap" });
    t.session.dispose();
  });
});

it("applies a retained geometry barrier after pending old-socket writes before resuming", async () => {
  const t = setup();
  await t.start();
  t.ready();
  t.terminal.delayed = true;
  t.socket().frame(1, "old width");
  t.socket().control({ type: "geometry", epoch: 1, sequence: 1, cols: 100, rows: 30 });
  t.socket().frame(2, "new width");
  t.socket().end();
  t.session.resumeExisting();
  t.terminal.drain();
  await Promise.resolve();
  t.socket().open();
  expect(t.socket().sent.at(-1)).toEqual({ type: "attach", resume_after: 2, cols: 100, rows: 30 });
  t.session.dispose();
});

it("suspends for bfcache without replaying input and disposes subscriptions", async () => {
  const t = setup();
  await t.start();
  t.ready();
  const old = t.socket();
  t.session.suspend();
  t.session.resumeExisting();
  t.session.paste("discarded");
  expect(t.sockets).toHaveLength(1);
  expect(t.enabled()).toBe(false);
  t.session.resume();
  t.session.resumeExisting();
  await Promise.resolve();
  t.socket().open();
  t.socket().control({ type: "attached", epoch: 2 });
  t.ready(2);
  old.frame(1, "stale");
  expect(t.terminal.output).toEqual([]);
  expect(t.socket().sent.filter((x) => x.type === "input")).toEqual([]);
  t.session.dispose();
  expect(t.terminal.input).toBeUndefined();
  t.session.resumeExisting();
  t.session.startFresh();
  expect(t.sockets).toHaveLength(2);
  expect(t.stopped()).toBe(1);
});

it("stops a late page admission without opening a socket after disposal", async () => {
  const terminal = new TestTerminal();
  let finish!: (value: import("./session").PreparedConnection) => void;
  let stopped = 0;
  let opened = 0;
  const session = createSession({
    terminal,
    input: { onInput: () => ({ dispose() {} }), setUserEnabled() {} },
    geometry: { measure: () => ({ cols: 80, rows: 24 }), apply() {} },
    onState: () => {},
    prepare: () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  });
  session.startFresh();
  session.dispose();
  finish({
    credential: "page-secret",
    stop: async () => {
      stopped++;
    },
    createSocket: () => {
      opened++;
      return new TestSocket() as unknown as WebSocket;
    },
  });
  await Promise.resolve();
  expect(stopped).toBe(1);
  expect(opened).toBe(0);
});

it.each([
  [500, 300, 500, 300],
  [501, 24, 500, 24],
  [80, 301, 80, 300],
  [501, 301, 500, 300],
  [0, 24, 1, 24],
  [80, 0, 80, 1],
  [NaN, NaN, 80, 24],
  [Infinity, -Infinity, 80, 24],
  [-Infinity, Infinity, 80, 24],
  [NaN, 40, 80, 40],
  [120, NaN, 120, 24],
])(
  "settles measured geometry %i x %i at bounded %i x %i",
  async (cols, rows, boundedCols, boundedRows) => {
    const t = setup();
    t.resize(cols, rows);
    await t.start();
    t.socket().control({ type: "replay_complete", epoch: 1, sequence: 0 });
    expect(t.socket().sent.at(-1)).toEqual({
      type: "resize",
      epoch: 1,
      cols: boundedCols,
      rows: boundedRows,
    });
    t.socket().control({ type: "resized", epoch: 1, cols: boundedCols, rows: boundedRows });
    expect(t.socket().sent.filter((frame) => frame.type === "resize")).toHaveLength(1);
    expect(t.states.at(-1)?.phase).toBe("connected");
    expect(t.enabled()).toBe(true);
    t.terminal.input?.({ source: "user", data: "usable" });
    expect(t.socket().sent.at(-1)).toEqual({
      type: "input",
      epoch: 1,
      source: "user",
      data: "usable",
    });
    t.session.dispose();
  },
);
