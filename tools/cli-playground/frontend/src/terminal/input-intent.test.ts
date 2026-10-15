import { expect, it } from "vite-plus/test";
import { Terminal } from "@xterm/xterm";
import { createInputIntent } from "./input-intent";

it("preserves parser responses during user gating and disposes its owned bridge", async () => {
  const terminal = new Terminal();
  const adapter = createInputIntent(terminal);
  const events: unknown[] = [];
  adapter.onInput((event) => events.push(event));
  terminal.input("blocked", true);
  terminal.input("parser", false);
  expect(events).toEqual([{ data: "parser", source: "terminal" }]);
  adapter.setUserEnabled(true);
  terminal.input("user", true);
  expect(events.at(-1)).toEqual({ data: "user", source: "user" });
  adapter.dispose();
  terminal.input("after", true);
  expect(events).toHaveLength(2);
  terminal.dispose();
});
it("fails clearly for an unsupported runtime shape", () => {
  expect(() => createInputIntent({} as Terminal)).toThrow(/Unsupported xterm/);
});

it("does not break a later bridge owner when disposed", () => {
  const terminal = new Terminal();
  const adapter = createInputIntent(terminal);
  const core = (
    terminal as unknown as {
      _core: { coreService: { triggerDataEvent: (data: string, user?: boolean) => void } };
    }
  )._core.coreService;
  const previous = core.triggerDataEvent;
  const wrapper = (data: string, user = false) => previous.call(core, data, user);
  core.triggerDataEvent = wrapper;
  const seen: string[] = [];
  terminal.onData((data) => seen.push(data));
  adapter.dispose();
  terminal.input("still works", true);
  expect(core.triggerDataEvent).toBe(wrapper);
  expect(seen).toEqual(["still works"]);
  terminal.dispose();
});
