import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, rmSync, truncateSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "vite-plus/test";
import { nativeArchive } from "./archive";

test("native fixture archives binary bytes beyond the child-process stdout limit", () => {
  const root = mkdtempSync(join(tmpdir(), "boxie-archive-test-"));
  try {
    const binary = Buffer.alloc(2 * 1024 * 1024, 0x61);
    binary.write("\x7fELF");
    const path = join(root, "echor");
    writeFileSync(path, binary);
    const archive = nativeArchive(path);
    const inspected = JSON.parse(
      execFileSync(
        "python3",
        [
          "-c",
          "import hashlib,io,json,sys,zipfile; z=zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())); print(json.dumps({'names':z.namelist(),'sha256':hashlib.sha256(z.read('echor')).hexdigest()}))",
        ],
        { input: archive, encoding: "utf8", timeout: 10_000, maxBuffer: 4096 },
      ),
    );
    expect(inspected).toEqual({
      names: ["echor"],
      sha256: createHash("sha256").update(binary).digest("hex"),
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("native fixture rejects a binary exceeding the product member limit", () => {
  const root = mkdtempSync(join(tmpdir(), "boxie-archive-test-"));
  try {
    const path = join(root, "echor");
    writeFileSync(path, "");
    truncateSync(path, 64 * 1024 * 1024 + 1);
    expect(() => nativeArchive(path)).toThrow(/binary exceeds/i);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
