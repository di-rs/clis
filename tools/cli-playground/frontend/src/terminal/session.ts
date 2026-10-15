export interface SessionState {
  phase: "connecting" | "connected" | "disconnected";
  label: string;
  message: string | null;
  canReconnect: boolean;
  absoluteDeadline?: string;
  reason?: string | null;
}
export interface TerminalPort {
  readonly cols: number;
  readonly rows: number;
  write(data: Uint8Array, callback?: () => void): void;
  paste(data: string): void;
  focus(): void;
  reset(): void;
}
export interface InputEvent {
  data: string;
  source: "user" | "terminal";
}
export interface InputIntent {
  onInput(listener: (event: InputEvent) => void): { dispose(): void };
  setUserEnabled(enabled: boolean): void;
}
export interface PreparedConnection {
  createSocket: () => WebSocket;
  credential: string;
  stop: () => Promise<void>;
}
interface Size {
  cols: number;
  rows: number;
}
interface SessionOptions {
  terminal: TerminalPort;
  input: InputIntent;
  geometry: { measure(): Size; apply(size: Size): void };
  prepare: () => Promise<PreparedConnection>;
  onState: (state: SessionState) => void;
}

export function createSession({ terminal, input, geometry, prepare, onState }: SessionOptions) {
  let socket: WebSocket | null = null;
  let binding: PreparedConnection | null = null;
  let detach: (() => void) | undefined;
  let disposed = false,
    suspended = false,
    ended = false;
  let attempt = 0,
    sandbox = 0,
    epoch = 0;
  let queued = 0,
    applied = 0,
    queuedBytes = 0;
  let synced: Size = { cols: terminal.cols, rows: terminal.rows };
  let resizing: Size | null = null;
  let timer: ReturnType<typeof setInterval> | undefined;
  let state: SessionState = {
    phase: "disconnected",
    label: "Disconnected",
    message: null,
    canReconnect: false,
  };
  input.setUserEnabled(false);

  function update(next: SessionState) {
    state = next;
    input.setUserEnabled(next.phase === "connected");
    onState(next);
  }
  function release(reason: string) {
    attempt++;
    epoch = 0;
    resizing = null;
    input.setUserEnabled(false);
    if (timer) clearInterval(timer);
    timer = undefined;
    const old = socket;
    socket = null;
    detach?.();
    detach = undefined;
    old?.close(1000, reason);
  }
  function disconnected(message = "Connection lost. Reconnect to continue this session.") {
    release("Disconnected");
    update({
      ...state,
      phase: "disconnected",
      label: "Disconnected",
      message,
      canReconnect: !!binding && !ended,
    });
  }
  function send(message: object) {
    if (!socket || socket.readyState !== 1 || disposed || suspended) return false;
    const wire = JSON.stringify(message);
    if (socket.bufferedAmount + new TextEncoder().encode(wire).length > 65536) {
      disconnected("Connection is too slow. Reconnect to continue.");
      return false;
    }
    try {
      socket.send(wire);
      return true;
    } catch {
      disconnected();
      return false;
    }
  }
  const subscription = input.onInput(({ data, source }) => {
    if (!epoch || !socket || (source === "user" && state.phase !== "connected")) return;
    let chunk = "";
    for (const character of data) {
      chunk += character;
      if (chunk.length >= 2048) {
        if (!send({ type: "input", epoch, source, data: chunk })) return;
        chunk = "";
      }
    }
    if (chunk) send({ type: "input", epoch, source, data: chunk });
  });
  function requestSize() {
    if (!epoch || resizing) return;
    const size = geometry.measure();
    resizing = {
      cols: Math.max(1, Math.min(500, size.cols)),
      rows: Math.max(1, Math.min(300, size.rows)),
    };
    update({ ...state, phase: "connecting", label: "Synchronizing", canReconnect: false });
    send({ type: "resize", epoch, ...resizing });
  }
  function syncGeometry() {
    if (state.phase === "connected") requestSize();
  }
  function openSocket(pending: number) {
    if (disposed || suspended || pending !== attempt || !binding) return;
    let next: WebSocket;
    try {
      next = binding.createSocket();
    } catch {
      disconnected();
      return;
    }
    socket = next;
    next.binaryType = "arraybuffer";
    const current = () => !disposed && !suspended && socket === next && pending === attempt;
    const owner = sandbox;
    let lastResponse = Date.now(),
      nonce = 0,
      lastPong = 0;
    const open = () => {
      if (current()) send({ type: "auth", credential: binding!.credential });
    };
    const message = (event: MessageEvent<unknown>) => {
      if (!current()) return;
      if (event.data instanceof ArrayBuffer) {
        if (!epoch || event.data.byteLength < 9 || event.data.byteLength > 16392) {
          disconnected("Invalid terminal frame.");
          return;
        }
        const sequence = Number(new DataView(event.data).getBigUint64(0));
        if (!Number.isSafeInteger(sequence) || sequence < 1) {
          disconnected("Invalid output sequence.");
          return;
        }
        if (sequence <= queued) return;
        if (sequence !== queued + 1) {
          ended = true;
          disconnected("Output history is unavailable. Start a new session.");
          return;
        }
        const bytes = new Uint8Array(event.data, 8);
        if (queuedBytes + bytes.length > 65536) {
          disconnected("Terminal output is too slow. Reconnect to continue.");
          return;
        }
        queued = sequence;
        queuedBytes += bytes.length;
        const writeEpoch = epoch;
        terminal.write(bytes, () => {
          if (owner !== sandbox) return;
          applied = sequence;
          queuedBytes -= bytes.length;
          // Same-sandbox writes remain applied after transport loss. They never
          // acknowledge on a different socket or attachment.
          if (current() && epoch === writeEpoch) send({ type: "ack", epoch, sequence: applied });
        });
        return;
      }
      if (typeof event.data !== "string") return;
      let data: Record<string, unknown>;
      try {
        const value: unknown = JSON.parse(event.data);
        if (typeof value !== "object" || value === null) return;
        data = value as Record<string, unknown>;
      } catch {
        return;
      }
      if (
        data.type === "authenticated" &&
        data.state === "ready" &&
        typeof data.absolute_deadline === "string"
      ) {
        state = { ...state, absoluteDeadline: data.absolute_deadline };
        send({ type: "attach", resume_after: applied, ...synced });
      } else if (
        data.type === "attached" &&
        Number.isSafeInteger(data.epoch) &&
        Number(data.epoch) > 0 &&
        !epoch
      ) {
        epoch = Number(data.epoch);
        lastResponse = Date.now();
        timer = setInterval(() => {
          if (!current()) return;
          if (Date.now() - lastResponse >= 30000) {
            disconnected("Connection timed out. Reconnect to continue.");
            return;
          }
          send({ type: "heartbeat", epoch, nonce: ++nonce });
        }, 10000);
      } else if (data.type === "status" && typeof data.reason === "string") {
        ended = true;
        state = { ...state, reason: data.reason };
        disconnected(
          typeof data.message === "string"
            ? data.message
            : `Session ended: ${data.reason}. Start a new session.`,
        );
      } else if (data.epoch === epoch && epoch) {
        if (
          data.type === "geometry" &&
          data.sequence === queued &&
          Number.isInteger(data.cols) &&
          Number.isInteger(data.rows) &&
          Number(data.cols) >= 1 &&
          Number(data.cols) <= 500 &&
          Number(data.rows) >= 1 &&
          Number(data.rows) <= 300
        ) {
          const size = { cols: Number(data.cols), rows: Number(data.rows) };
          terminal.write(new Uint8Array(), () => {
            // Like queued bytes, an old socket's already-ordered geometry remains
            // part of this sandbox's screen after disconnect. Resume drains it.
            if (owner !== sandbox || disposed) return;
            if (terminal.cols !== size.cols || terminal.rows !== size.rows) geometry.apply(size);
            synced = size;
          });
        } else if (data.type === "replay_complete" && data.sequence === queued) {
          terminal.write(new Uint8Array(), () => {
            if (current()) requestSize();
          });
        } else if (
          data.type === "resized" &&
          resizing &&
          data.cols === resizing.cols &&
          data.rows === resizing.rows
        ) {
          const size = resizing;
          terminal.write(new Uint8Array(), () => {
            if (!current()) return;
            geometry.apply(size);
            synced = size;
            resizing = null;
            update({
              ...state,
              phase: "connected",
              label: "Connected",
              message: null,
              reason: null,
              canReconnect: false,
            });
            terminal.focus();
            const desired = geometry.measure();
            if (desired.cols !== synced.cols || desired.rows !== synced.rows) requestSize();
          });
        } else if (
          data.type === "heartbeat" &&
          typeof data.nonce === "number" &&
          data.nonce > lastPong &&
          data.nonce <= nonce
        ) {
          lastPong = data.nonce;
          lastResponse = Date.now();
        }
      }
    };
    const close = () => {
      if (current()) disconnected(state.message || undefined);
    };
    next.addEventListener("open", open);
    next.addEventListener("message", message);
    next.addEventListener("close", close);
    next.addEventListener("error", close);
    detach = () => {
      next.removeEventListener("open", open);
      next.removeEventListener("message", message);
      next.removeEventListener("close", close);
      next.removeEventListener("error", close);
    };
  }
  function startFresh() {
    if (disposed || suspended) return;
    release("Fresh session");
    const pending = attempt;
    const previous = binding;
    binding = null;
    void previous?.stop().catch(() => {});
    ended = false;
    update({ phase: "connecting", label: "Connecting", message: null, canReconnect: false });
    terminal.write(new Uint8Array(), () => {
      if (disposed || suspended || pending !== attempt) return;
      sandbox++;
      queued = applied = queuedBytes = 0;
      terminal.reset();
      synced = { cols: terminal.cols, rows: terminal.rows };
      void prepare()
        .then((prepared) => {
          if (disposed || suspended || pending !== attempt) {
            void prepared.stop().catch(() => {});
            return;
          }
          binding = prepared;
          openSocket(pending);
        })
        .catch(() => {
          if (pending === attempt) disconnected("Could not start a session. Try a new session.");
        });
    });
  }
  function resumeExisting() {
    if (disposed || suspended || !binding || ended) return;
    release("Reconnect");
    const pending = attempt;
    update({
      ...state,
      phase: "connecting",
      label: "Reconnecting",
      message: null,
      canReconnect: false,
    });
    terminal.write(new Uint8Array(), () => {
      if (pending === attempt) openSocket(pending);
    });
  }
  return {
    startFresh,
    resumeExisting,
    syncGeometry,
    disconnect: () => {
      if (!disposed) disconnected("Disconnected. Reconnect to continue this session.");
    },
    paste: (command: string) => {
      if (state.phase === "connected" && !disposed && !suspended) {
        terminal.paste(command);
        terminal.focus();
      }
    },
    suspend() {
      if (disposed) return;
      suspended = true;
      disconnected("Disconnected. Reconnect to continue this session.");
    },
    resume() {
      if (!disposed) suspended = false;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      release("Terminal disposed");
      subscription.dispose();
      const previous = binding;
      binding = null;
      void previous?.stop().catch(() => {});
    },
  };
}
