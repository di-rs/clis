import { describe, expect, it } from "vite-plus/test";
import { Terminal } from "@xterm/xterm";
import { createSession, type SessionState, type TerminalPort } from "./session";

class TestTerminal implements TerminalPort {
  cols = 80;
  rows = 24;
  options = { disableStdin: true };
  output: Uint8Array[] = [];
  pastes: string[] = [];
  resets = 0;
  focused = false;
  input?: (data: string) => void;
  resize?: (size: { cols: number; rows: number }) => void;
  write(data: Uint8Array, callback?: () => void) {
    this.output.push(data);
    callback?.();
  }
  paste(data: string) {
    this.pastes.push(data);
    this.input?.(data);
  }
  focus() {
    this.focused = true;
  }
  reset() {
    this.resets++;
    this.output = [];
  }
  onData(listener: (data: string) => void) {
    this.input = listener;
    return {
      dispose: () => {
        this.input = undefined;
      },
    };
  }
  onResize(listener: (size: { cols: number; rows: number }) => void) {
    this.resize = listener;
    return {
      dispose: () => {
        this.resize = undefined;
      },
    };
  }
}

class TestSocket extends EventTarget {
  readyState = 0;
  binaryType: BinaryType = "blob";
  sent: unknown[] = [];
  frames: string[] = [];
  closed = false;
  send(data: string) {
    this.frames.push(data);
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.closed = true;
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.dispatchEvent(new Event("open"));
  }
  message(data: unknown) {
    this.dispatchEvent(new MessageEvent("message", { data }));
  }
  end(code = 1000) {
    this.readyState = 3;
    const event = new Event("close");
    Object.defineProperty(event, "code", { value: code });
    this.dispatchEvent(event);
  }
}

function setup() {
  const terminal = new TestTerminal();
  const sockets: TestSocket[] = [];
  const states: SessionState[] = [];
  let fits = 0;
  const session = createSession({
    terminal,
    fit: () => {
      fits++;
    },
    createSocket: () => {
      const socket = new TestSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
    onState: (state) => states.push(state),
  });
  const currentSocket = () => sockets.at(-1)!;
  const state = () => states.at(-1)!;
  return { terminal, sockets, session, currentSocket, state, fits: () => fits };
}

describe("terminal session", () => {
  it.each([
    { name: "plain text", text: "a".repeat(17_000) },
    { name: "JSON escapes", text: '\u0000\n\r\t"\\'.repeat(4_000) },
    { name: "Unicode", text: "é漢🙂".repeat(8_000) },
    {
      name: "bracketed paste",
      text: `\x1b[200~${"a".repeat(2_041)}🙂${"b".repeat(17_000)}\x1b[201~`,
    },
  ])("sends $name within gateway limits without changing the input stream", ({ text }) => {
    const test = setup();
    test.session.connect();
    test.currentSocket().open();
    test.terminal.input?.(text);
    const frames = test.currentSocket().frames.slice(1);
    const chunks = frames.map((frame) => (JSON.parse(frame) as { data: string }).data);
    expect(chunks.join("")).toBe(text);
    for (const [index, frame] of frames.entries()) {
      expect(frame.length).toBeLessThanOrEqual(16_384);
      expect(new TextEncoder().encode(frame).length).toBeLessThan(65_536);
      expect(new TextDecoder().decode(new TextEncoder().encode(chunks[index]))).toBe(chunks[index]);
    }
  });

  it("clears xterm's pending writes before starting a replacement session", async () => {
    const terminal = new Terminal();
    const sockets: TestSocket[] = [];
    const session = createSession({
      terminal,
      fit: () => {},
      createSocket: () => {
        const socket = new TestSocket();
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
      onState: () => {},
    });
    const drained = () => new Promise<void>((resolve) => terminal.write(new Uint8Array(), resolve));
    try {
      session.connect();
      await drained();
      sockets[0].open();
      sockets[0].message(new TextEncoder().encode("OLD SESSION").buffer);
      session.connect();
      await drained();
      expect(terminal.buffer.active.getLine(0)?.translateToString(true)).toBe("");
      sockets[1].open();
      sockets[1].message(new TextEncoder().encode("NEW SESSION").buffer);
      await drained();
      expect(terminal.buffer.active.getLine(0)?.translateToString(true)).toBe("NEW SESSION");
    } finally {
      session.dispose();
      terminal.dispose();
    }
  });

  it("enables input only after opening and sends the current terminal size", () => {
    const test = setup();
    test.session.connect();
    test.terminal.input?.("too early");
    expect(test.currentSocket().sent).toEqual([]);
    expect(test.state().phase).toBe("connecting");
    test.currentSocket().open();
    test.terminal.input?.("pwd\r");
    test.terminal.resize?.({ cols: 100, rows: 30 });
    expect(test.currentSocket().sent).toEqual([
      { type: "resize", cols: 80, rows: 24 },
      { type: "input", data: "pwd\r" },
      { type: "resize", cols: 100, rows: 30 },
    ]);
    expect(test.terminal.options.disableStdin).toBe(false);
    expect(test.state().phase).toBe("connected");
    expect(test.fits()).toBe(1);
  });

  it("pastes examples without executing them and ignores them while disconnected", () => {
    const test = setup();
    test.session.paste("cat welcome.txt");
    expect(test.terminal.pastes).toEqual([]);
    test.session.connect();
    test.currentSocket().open();
    test.session.paste("cat welcome.txt");
    expect(test.terminal.pastes).toEqual(["cat welcome.txt"]);
    expect(test.currentSocket().sent.at(-1)).toEqual({ type: "input", data: "cat welcome.txt" });
    expect(test.terminal.focused).toBe(true);
  });

  it("reset closes the old socket and ignores late output, close, and open events", () => {
    const test = setup();
    test.session.connect();
    const old = test.currentSocket();
    old.open();
    old.message(new Uint8Array([65]).buffer);
    expect(test.terminal.output).toEqual([new Uint8Array([65])]);
    test.session.connect();
    expect(old.closed).toBe(true);
    old.message(new Uint8Array([66]).buffer);
    old.end();
    old.open();
    expect(test.state().phase).toBe("connecting");
    expect(test.terminal.output).toEqual([]);
    test.currentSocket().open();
    test.currentSocket().message(new Uint8Array([67]).buffer);
    expect(test.terminal.output).toEqual([new Uint8Array([67])]);
    expect(test.terminal.resets).toBe(2);
  });

  it("keeps output on disconnect and starts a clean workspace on reconnect", () => {
    const test = setup();
    test.session.connect();
    const old = test.currentSocket();
    old.open();
    old.message(new Uint8Array([65]).buffer);
    test.session.disconnect();
    test.terminal.input?.("ignored");
    expect(test.terminal.output).toEqual([new Uint8Array([65])]);
    expect(test.terminal.options.disableStdin).toBe(true);
    expect(test.state()).toMatchObject({ phase: "disconnected", canReconnect: true });
    expect(old.sent).toHaveLength(1);
    test.session.connect();
    expect(test.sockets).toHaveLength(2);
    expect(test.terminal.output).toEqual([]);
  });

  it("retains the server reason after close and safely ignores unrelated messages", () => {
    const test = setup();
    test.session.connect();
    const socket = test.currentSocket();
    socket.open();
    for (const value of ["null", "{}", "{", "[]", '{"type":"status","message":12}'])
      socket.message(value);
    expect(test.state().message).toBeNull();
    socket.message('{"type":"status","message":"Session limit reached"}');
    expect(test.state()).toMatchObject({ message: "Session limit reached", canReconnect: false });
    socket.end(1013);
    expect(test.state()).toMatchObject({
      phase: "disconnected",
      message: "Session limit reached",
      canReconnect: true,
    });
  });

  it("turns transport errors into a recoverable disconnected state", () => {
    const test = setup();
    test.session.connect();
    test.currentSocket().dispatchEvent(new Event("error"));
    expect(test.state()).toMatchObject({ phase: "disconnected", canReconnect: true });
    expect(test.currentSocket().closed).toBe(true);
    expect(test.terminal.options.disableStdin).toBe(true);
    test.session.connect();
    test.currentSocket().open();
    expect(test.state().phase).toBe("connected");
  });

  it("suspends on pagehide and restores a disconnected state after bfcache return", () => {
    const test = setup();
    test.session.connect();
    test.currentSocket().open();
    test.session.suspend();
    test.session.connect();
    expect(test.sockets).toHaveLength(1);
    expect(test.currentSocket().closed).toBe(true);
    test.session.resume();
    expect(test.state()).toMatchObject({ phase: "disconnected", canReconnect: true });
    test.session.connect();
    expect(test.sockets).toHaveLength(2);
  });

  it("disposes terminal subscriptions and prevents reconnection or stale output", () => {
    const test = setup();
    test.session.connect();
    const socket = test.currentSocket();
    socket.open();
    test.session.dispose();
    test.session.dispose();
    socket.message(new Uint8Array([65]).buffer);
    test.session.connect();
    test.session.paste("ignored");
    expect(socket.closed).toBe(true);
    expect(test.terminal.input).toBeUndefined();
    expect(test.terminal.resize).toBeUndefined();
    expect(test.terminal.options.disableStdin).toBe(true);
    expect(test.terminal.output).toEqual([]);
    expect(test.sockets).toHaveLength(1);
  });
});
