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
