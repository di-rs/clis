import type { Argument, Catalog, Command } from "../src/catalog";

// Hand-authored interfaces keep browser and comparison expectations independent
// of the exporter and server validators.
export function argument(overrides: Partial<Argument> = {}): Argument {
  return {
    id: "help",
    short: "h",
    long: "help",
    aliases: [],
    help: "Print help",
    long_help: null,
    value_names: [],
    default_values: [],
    possible_values: [],
    required: false,
    index: null,
    num_args: { min: 0, max: 0 },
    action: "help",
    global: false,
    owner: [],
    ...overrides,
  };
}
export function command(overrides: Partial<Command> = {}): Command {
  return {
    name: "catr",
    aliases: [],
    about: "Read and concatenate files",
    long_about: null,
    usage: "Usage: catr [OPTIONS] [FILE]...",
    usage_override: null,
    args: [argument()],
    subcommands: [],
    ...overrides,
  };
}
export function catalog(sha: string, names: string[] = ["catr"]): Catalog {
  return {
    schema_version: 1,
    head_sha: sha,
    base_sha: "d".repeat(40),
    clis: names.map((name) => ({
      name,
      binary_sha256: "f".repeat(64),
      comparison: "available",
      head: {
        schema_version: 1,
        command: command({
          name,
          args: [
            argument(),
            argument({
              id: "number",
              short: "n",
              long: "number",
              help: "Number each line",
              action: "set_true",
            }),
            argument({
              id: "format",
              short: null,
              long: "format",
              help: "Choose the output format",
              long_help:
                "Choose plain text or structured output. No value is inserted automatically.",
              value_names: ["FORMAT"],
              default_values: ["text"],
              possible_values: ["text", "json"],
              num_args: { min: 1, max: 1 },
              action: "set",
            }),
          ],
        }),
      },
      base: {
        schema_version: 1,
        command: command({
          name,
          args: [
            argument(),
            argument({
              id: "number",
              short: "n",
              long: "number",
              help: "Number output",
              action: "set_true",
            }),
            argument({
              id: "old",
              short: null,
              long: "old",
              help: "The old option",
              action: "set_true",
            }),
          ],
        }),
      },
    })),
  };
}
