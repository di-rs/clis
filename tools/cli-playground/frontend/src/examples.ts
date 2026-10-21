export interface Example {
  label: string;
  command: string;
  display: string;
}

export const files = [
  "welcome.txt",
  "people.csv",
  "app.log",
  "duplicates.txt",
  "sample.xml",
  "nested/notes.txt",
];

const cliArguments: Record<string, readonly (readonly [string, string])[]> = {
  catr: [
    ["Read a file", "welcome.txt"],
    ["Number the lines", "-n welcome.txt"],
  ],
  headr: [
    ["First three lines", "-n 3 app.log"],
    ["Read a file", "welcome.txt"],
  ],
  tailr: [
    ["Last three lines", "-n 3 app.log"],
    ["Read a file", "welcome.txt"],
  ],
  grepr: [
    ["Find a match", "ERROR app.log"],
    ["Ignore case", "-i hello welcome.txt"],
  ],
  cutr: [
    ["Select a CSV column", "-d , -f 1 people.csv"],
    ["First ten characters", "-c 1-10 welcome.txt"],
  ],
  wcr: [
    ["Count a file", "welcome.txt"],
    ["Count lines", "-l app.log"],
  ],
  uniqr: [
    ["Remove repeated lines", "duplicates.txt"],
    ["Count repeated lines", "-c duplicates.txt"],
  ],
  lsr: [
    ["List the workspace", "."],
    ["Show file details", "-l ."],
  ],
  findr: [
    ["Explore the workspace", "."],
    ["Find text files", ". -n '.*\\.txt$'"],
  ],
  echor: [
    ["Say hello", "'hello, playground'"],
    ["Without a newline", "-n 'hello'"],
  ],
  pwdr: [["Where am I?", ""]],
  mkdirr: [["Create a directory", "my-directory"]],
  touch: [["Create a file", "my-file.txt"]],
  commr: [["Compare sorted files", "<(sort welcome.txt) <(sort duplicates.txt)"]],
  calr: [
    ["This month", ""],
    ["A month and year", "-m 2 2024"],
  ],
  parsu: [["Parse an element tree", "sample.xml"]],
  biggie: [
    ["Generate five lines", "text -s 42 -n 5 -"],
    ["Repeat a record", "records -r hello -p 3 -"],
  ],
  kara: [
    ["Open the editor", "welcome.txt"],
    ["Start an empty buffer", ""],
  ],
  true: [["Inspect the exit status", "; printf 'exit: %s\\n' \"$?\""]],
  false: [["Inspect the exit status", "; printf 'exit: %s\\n' \"$?\""]],
};

export function examplesFor(cli: string): Example[] {
  const choices = [...(cliArguments[cli] ?? [])];
  if (!["true", "false", "kara"].includes(cli)) choices.unshift(["Explore the options", "--help"]);
  return choices.slice(0, 3).map(([label, args]) => {
    const separator = args.startsWith(";") || !args ? "" : " ";
    return {
      label,
      command: `/opt/artifact/${cli}${separator}${args}`,
      display: `${cli}${separator}${args}`,
    };
  });
}

export const shellExamples: Example[] = [
  { label: "Pipe output", command: "cat welcome.txt | wc -l", display: "cat welcome.txt | wc -l" },
  {
    label: "Write a file",
    command: "printf 'hello\\n' > scratch.txt",
    display: "printf 'hello\\n' > scratch.txt",
  },
  {
    label: "Chain commands",
    command: "mkdir -p demo && ls demo",
    display: "mkdir -p demo && ls demo",
  },
];
