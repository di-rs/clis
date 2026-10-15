import type { Terminal } from "@xterm/xterm";
import xtermPackage from "@xterm/xterm/package.json";
import type { InputEvent, InputIntent } from "./session";

// xterm 6.0.0's public onData omits provenance. This is the sole private-API
// dependency: revalidate keyboard/paste/composition/parser browser tests on upgrade.
interface CoreService {
  triggerDataEvent: (this: CoreService, data: string, wasUserInput?: boolean) => void;
}
export function createInputIntent(terminal: Terminal): InputIntent & { dispose(): void } {
  const runtime = terminal as unknown as { _core?: { coreService?: CoreService } };
  const core = runtime._core?.coreService;
  if (
    xtermPackage.version !== "6.0.0" ||
    !core ||
    typeof core.triggerDataEvent !== "function" ||
    core.triggerDataEvent.length !== 1
  )
    throw new Error("Unsupported xterm input provenance runtime (requires xterm 6.0.0).");
  const original = core.triggerDataEvent;
  const listeners = new Set<(event: InputEvent) => void>();
  let enabled = false,
    disposed = false;
  function bridge(this: CoreService, data: string, wasUserInput = false) {
    if (disposed) {
      original.call(this, data, wasUserInput);
      return;
    }
    if (wasUserInput && !enabled) return;
    const accepted = !!data && !terminal.options.disableStdin;
    original.call(this, data, wasUserInput);
    if (accepted)
      for (const listener of listeners)
        listener({ data, source: wasUserInput ? "user" : "terminal" });
  }
  core.triggerDataEvent = bridge;
  return {
    setUserEnabled(value) {
      enabled = value;
    },
    onInput(listener) {
      listeners.add(listener);
      return { dispose: () => listeners.delete(listener) };
    },
    dispose() {
      disposed = true;
      listeners.clear();
      if (core.triggerDataEvent === bridge) core.triggerDataEvent = original;
    },
  };
}
