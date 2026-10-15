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
  options: { disableStdin?: boolean };
  write(data: Uint8Array, callback?: () => void): void;
  paste(data: string): void;
  focus(): void;
  reset(): void;
  onData(listener: (data: string) => void): { dispose(): void };
  onResize(listener: (size: { cols: number; rows: number }) => void): { dispose(): void };
}

export interface PreparedConnection {
  createSocket: () => WebSocket;
  credential: string;
  stop: () => Promise<void>;
}

interface SessionOptions {
  terminal: TerminalPort;
  fit: () => void;
  prepare: () => Promise<PreparedConnection>;
  onState: (state: SessionState) => void;
}

export function createSession({ terminal, fit, prepare, onState }: SessionOptions) {
  let socket: WebSocket | null = null;
  let binding: PreparedConnection | null = null;
  let detachSocket: (() => void) | undefined;
  let disposed = false;
  let suspended = false;
  let serverMessage = "";
  let connection = 0;
  let state: SessionState = {
    phase: "disconnected",
    label: "Disconnected",
    message: null,
    canReconnect: false,
  };
  terminal.options.disableStdin = true;

  function update(next: SessionState) {
    state = next;
    terminal.options.disableStdin = next.phase !== "connected";
    onState(next);
  }

  function send(message: object) {
    if (!disposed && !suspended && state.phase === "connected" && socket?.readyState === 1) {
      socket.send(JSON.stringify(message));
    }
  }

  const input = terminal.onData((data) => {
    let chunk = "";
    // Even six-character JSON escapes fit below the gateway's 16 KiB limit.
    // Iterating code points keeps surrogate pairs together across messages.
    for (const character of data) {
      chunk += character;
      if (chunk.length >= 2048) {
        send({ type: "input", data: chunk });
        chunk = "";
      }
    }
    if (chunk) send({ type: "input", data: chunk });
  });
  const resize = terminal.onResize(({ cols, rows }) => send({ type: "resize", cols, rows }));

  function releaseSocket(reason: string, close = true, stop = true) {
    connection++;
    const previous = socket;
    socket = null;
    detachSocket?.();
    detachSocket = undefined;
    if (close) previous?.close(1000, reason);
    if (stop) {
      const oldBinding = binding;
      binding = null;
      void oldBinding?.stop().catch(() => {});
    }
  }

  function disconnected(message: string, label = "Disconnected") {
    update({ ...state, phase: "disconnected", label, message, canReconnect: true });
  }

  function connect() {
    if (disposed || suspended) return;
    releaseSocket("Reset session");
    serverMessage = "";
    update({ phase: "connecting", label: "Connecting", message: null, canReconnect: false });
    const pending = connection;
    // xterm writes asynchronously: drain the old output before resetting its
    // buffer and modes, then open the new session. Disconnect cancels this work.
    terminal.write(new Uint8Array(), () => {
      if (disposed || suspended || connection !== pending) return;
      terminal.reset();
      void openSocket(pending);
    });
  }

  async function openSocket(pending: number) {
    let next: WebSocket;
    try {
      const prepared = await prepare();
      if (disposed || suspended || connection !== pending) {
        await prepared.stop().catch(() => {});
        return;
      }
      binding = prepared;
      next = prepared.createSocket();
      next.binaryType = "arraybuffer";
      socket = next;
    } catch {
      if (disposed || suspended || connection !== pending) return;
      releaseSocket("Connection failed");
      disconnected(
        "Could not connect to the terminal. Try starting a new session.",
        "Connection failed",
      );
      return;
    }
    // Every handler belongs to one connection. Late events from a reset or closed
    // socket must never write into the replacement session or change its status.
    const current = () => !disposed && !suspended && socket === next;
    const open = () => {
      if (!current()) return;
      next.send(JSON.stringify({ type: "auth", credential: binding!.credential }));
    };
    const message = (event: MessageEvent<unknown>) => {
      if (!current()) return;
      if (event.data instanceof ArrayBuffer) {
        if (state.phase === "connected") terminal.write(new Uint8Array(event.data));
        return;
      }
      if (typeof event.data !== "string") return;
      try {
        const data: unknown = JSON.parse(event.data);
        if (typeof data !== "object" || data === null) return;
        const status = data as Record<string, unknown>;
        if (
          status.type === "authenticated" &&
          status.state === "ready" &&
          typeof status.absolute_deadline === "string" &&
          Number.isFinite(Date.parse(status.absolute_deadline))
        ) {
          update({
            phase: "connected",
            label: "Connected",
            message: null,
            canReconnect: false,
            absoluteDeadline: status.absolute_deadline,
            reason: null,
          });
          fit();
          send({ type: "resize", cols: terminal.cols, rows: terminal.rows });
          terminal.focus();
        }
        if (status.type === "status" && typeof status.message === "string") {
          serverMessage = status.message;
          update({
            ...state,
            message: serverMessage,
            reason: typeof status.reason === "string" ? status.reason : null,
            canReconnect: false,
          });
        }
      } catch {
        // The gateway sends binary PTY output or a structured terminal status.
      }
    };
    const error = () => {
      if (!current()) return;
      releaseSocket("Connection failed");
      disconnected(
        serverMessage || "Could not connect to the terminal. Try starting a new session.",
        "Connection failed",
      );
    };
    const close = (event: CloseEvent) => {
      if (!current()) return;
      releaseSocket("Session ended", false, false);
      disconnected(
        serverMessage ||
          (event.code === 1000
            ? "Session ended. Start a new session for a fresh workspace."
            : "The connection closed. Start a new session to continue in a fresh workspace."),
      );
    };
    next.addEventListener("open", open);
    next.addEventListener("message", message);
    next.addEventListener("error", error);
    next.addEventListener("close", close);
    detachSocket = () => {
      next.removeEventListener("open", open);
      next.removeEventListener("message", message);
      next.removeEventListener("error", error);
      next.removeEventListener("close", close);
    };
  }

  function disconnect() {
    if (disposed) return;
    releaseSocket("Visitor disconnected", true, false);
    disconnected(
      "Disconnected. Your terminal output stays here; a new session starts with fresh files.",
    );
  }

  return {
    connect,
    disconnect,
    paste: (command: string) => {
      if (disposed || suspended || state.phase !== "connected" || socket?.readyState !== 1) return;
      // xterm respects bracketed paste. No trailing newline: the visitor decides
      // when an example executes, including shell operators and redirections.
      terminal.paste(command);
      terminal.focus();
    },
    suspend() {
      if (disposed) return;
      suspended = true;
      releaseSocket("Page closed", true, false);
      disconnected("Disconnected. Start a new session to continue with fresh files.");
    },
    resume() {
      if (disposed) return;
      suspended = false;
      disconnect();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      releaseSocket("Terminal disposed");
      input.dispose();
      resize.dispose();
      terminal.options.disableStdin = true;
    },
  };
}
