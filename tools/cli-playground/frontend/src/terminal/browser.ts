import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { createSession, type SessionState } from "./session";

export type BrowserTerminal = ReturnType<typeof createBrowserTerminal>;

export function createBrowserTerminal(
  host: HTMLDivElement,
  environmentId: string,
  sha: string,
  revision: string,
  onState: (state: SessionState) => void,
) {
  const terminal = new Terminal({
    fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
    fontSize: matchMedia("(max-width: 740px)").matches ? 12 : 13,
    lineHeight: 1.5,
    letterSpacing: 0,
    cursorBlink: !matchMedia("(prefers-reduced-motion: reduce)").matches,
    cursorStyle: "bar",
    scrollback: 5000,
    screenReaderMode: true,
    allowProposedApi: false,
    disableStdin: true,
    theme: {
      background: "#0b0b0b",
      foreground: "#dedede",
      cursor: "#fafafa",
      cursorAccent: "#0b0b0b",
      selectionBackground: "#444444",
      selectionForeground: "#ffffff",
      black: "#252525",
      red: "#b6b6b6",
      green: "#e1e1e1",
      yellow: "#c5c5c5",
      blue: "#a5a5a5",
      magenta: "#bdbdbd",
      cyan: "#cecece",
      white: "#ededed",
      brightBlack: "#8a8a8a",
      brightRed: "#d0d0d0",
      brightGreen: "#ffffff",
      brightYellow: "#e2e2e2",
      brightBlue: "#bcbcbc",
      brightMagenta: "#d4d4d4",
      brightCyan: "#e3e3e3",
      brightWhite: "#ffffff",
    },
  });
  const fitAddon = new FitAddon();
  let resizeFrame = 0;
  let disposed = false;
  let observer: ResizeObserver | undefined;
  let session: ReturnType<typeof createSession> | undefined;
  const fit = () => {
    if (!disposed && host.clientWidth > 0 && host.clientHeight > 0) fitAddon.fit();
  };
  const queueFit = () => {
    if (disposed || resizeFrame) return;
    resizeFrame = requestAnimationFrame(() => {
      resizeFrame = 0;
      fit();
    });
  };
  const focus = () => terminal.focus();
  const hide = () => session?.suspend();
  const show = (event: PageTransitionEvent) => {
    if (event.persisted) {
      session?.resume();
      queueFit();
    }
  };
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    cancelAnimationFrame(resizeFrame);
    observer?.disconnect();
    host.removeEventListener("focus", focus);
    window.removeEventListener("resize", queueFit);
    window.removeEventListener("pagehide", hide);
    window.removeEventListener("pageshow", show);
    session?.dispose();
    // Disposing xterm also disposes its loaded addons and keyboard listeners.
    terminal.dispose();
  };

  try {
    terminal.loadAddon(fitAddon);
    terminal.open(host);
    terminal.attachCustomKeyEventHandler(
      (event) =>
        !(
          event.type === "keydown" &&
          (event.ctrlKey || event.metaKey) &&
          event.key.toLowerCase() === "c" &&
          terminal.hasSelection()
        ),
    );
    session = createSession({
      terminal,
      fit,
      createSocket: () => {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        return new WebSocket(
          `${protocol}//${window.location.host}/ws/${encodeURIComponent(environmentId)}?sha=${encodeURIComponent(sha)}&revision=${encodeURIComponent(revision)}`,
        );
      },
      onState,
    });
    observer = new ResizeObserver(queueFit);
    observer.observe(host);
    host.addEventListener("focus", focus);
    window.addEventListener("resize", queueFit);
    window.addEventListener("pagehide", hide);
    window.addEventListener("pageshow", show);
    queueFit();
    return {
      connect: session.connect,
      disconnect: session.disconnect,
      paste: session.paste,
      dispose,
    };
  } catch (error) {
    dispose();
    throw error;
  }
}
