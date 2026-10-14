export interface SessionState {
  phase: "connecting" | "connected" | "disconnected";
  label: string;
  message: string | null;
  canReconnect: boolean;
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

interface SessionOptions {
  terminal: TerminalPort;
  fit: () => void;
  createSocket: () => WebSocket;
  onState: (state: SessionState) => void;
}

export function createSession({ terminal, fit, createSocket, onState }: SessionOptions) {
  let socket: WebSocket | null = null;
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

  function releaseSocket(reason: string, close = true) {
    connection++;
    const previous = socket;
    socket = null;
    detachSocket?.();
    detachSocket = undefined;
    if (close) previous?.close(1000, reason);
  }

  function disconnected(message: string, label = "Disconnected") {
    update({ phase: "disconnected", label, message, canReconnect: true });
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
      openSocket();
    });
  }

  function openSocket() {
    let next: WebSocket;
    try {
      next = createSocket();
      next.binaryType = "arraybuffer";
      socket = next;
    } catch {
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
      update({ phase: "connected", label: "Connected", message: null, canReconnect: false });
      fit();
      send({ type: "resize", cols: terminal.cols, rows: terminal.rows });
      terminal.focus();
    };
    const message = (event: MessageEvent<unknown>) => {
      if (!current()) return;
      if (event.data instanceof ArrayBuffer) {
        terminal.write(new Uint8Array(event.data));
        return;
      }
      if (typeof event.data !== "string") return;
      try {
        const data: unknown = JSON.parse(event.data);
        if (typeof data !== "object" || data === null) return;
        const status = data as Record<string, unknown>;
        if (status.type === "status" && typeof status.message === "string") {
          serverMessage = status.message;
          update({ ...state, message: serverMessage, canReconnect: false });
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
      releaseSocket("Session ended", false);
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
    releaseSocket("Visitor disconnected");
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
      releaseSocket("Page closed");
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
