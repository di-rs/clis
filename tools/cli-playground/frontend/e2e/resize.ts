import { expect, type Page } from "@playwright/test";

interface Size {
  epoch: number;
  cols: number;
  rows: number;
}
interface ResizeObservation {
  socket: WebSocket | null;
  epoch: number;
  size: Size | null;
  waiting: {
    socket: WebSocket;
    epoch: number;
    previous: Size;
    request: Size | null;
    acknowledged: boolean;
  } | null;
}
declare global {
  interface Window {
    terminalResize: ResizeObservation;
  }
}

export async function observeTerminalResize(page: Page) {
  await page.addInitScript(() => {
    const trace: ResizeObservation = { socket: null, epoch: 0, size: null, waiting: null };
    window.terminalResize = trace;
    const Native = window.WebSocket;
    window.WebSocket = class extends Native {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols);
        trace.socket = this;
        trace.epoch = 0;
        trace.size = null;
        this.addEventListener("message", ({ data }) => {
          if (this !== trace.socket || typeof data !== "string") return;
          const frame = JSON.parse(data);
          if (frame.type === "attached") trace.epoch = frame.epoch;
          if (frame.type !== "resized" || frame.epoch !== trace.epoch) return;
          const size = { epoch: frame.epoch, cols: frame.cols, rows: frame.rows };
          trace.size = size;
          const waiting = trace.waiting;
          const request = waiting?.request;
          if (
            waiting?.socket === this &&
            request &&
            request.epoch === size.epoch &&
            request.cols === size.cols &&
            request.rows === size.rows
          )
            waiting.acknowledged = true;
        });
      }
      send(value: string | Blob | BufferSource) {
        super.send(value);
        const waiting = trace.waiting;
        if (waiting?.socket !== this || typeof value !== "string") return;
        const frame = JSON.parse(value);
        if (
          frame.type === "resize" &&
          frame.epoch === waiting.epoch &&
          (frame.cols !== waiting.previous.cols || frame.rows !== waiting.previous.rows)
        ) {
          waiting.request = { epoch: frame.epoch, cols: frame.cols, rows: frame.rows };
          waiting.acknowledged = false;
        }
      }
    };
  });
}

export async function resizeTerminalViewport(
  page: Page,
  viewport: { width: number; height: number },
) {
  await page.evaluate(() => {
    const trace = window.terminalResize;
    if (!trace.socket || !trace.size || !trace.epoch)
      throw new Error("An attached terminal with acknowledged geometry is required");
    trace.waiting = {
      socket: trace.socket,
      epoch: trace.epoch,
      previous: trace.size,
      request: null,
      acknowledged: false,
    };
  });
  try {
    await page.setViewportSize(viewport);
    // Old Connected and a transient Synchronizing state are insufficient: the
    // current socket must acknowledge this viewport's new requested geometry.
    await expect
      .poll(() =>
        page.evaluate(() => {
          const trace = window.terminalResize;
          return (
            trace.waiting?.socket === trace.socket &&
            trace.waiting?.acknowledged &&
            document.querySelector("#connection-status")?.textContent === "Connected"
          );
        }),
      )
      .toBe(true);
  } finally {
    await page.evaluate(() => {
      window.terminalResize.waiting = null;
    });
  }
}
