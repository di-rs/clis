import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// The product accepts 64 MiB/member and 512 MiB/archive. This fixture has one
// stored member, so bound its body more tightly to that member plus ZIP metadata.
const maxBinary = 64 * 1024 * 1024;
const maxArchive = maxBinary + 1024;

export function nativeArchive(binary: string): Buffer {
  if (!statSync(binary).isFile() || statSync(binary).size > maxBinary)
    throw new Error("native binary exceeds the product member limit");
  const root = mkdtempSync(join(tmpdir(), "boxie-native-archive-"));
  try {
    const archive = join(root, "bundle.zip");
    execFileSync(
      "python3",
      [
        "-c",
        `import sys,zipfile
with open(sys.argv[1], 'rb') as source, zipfile.ZipFile(sys.argv[2], 'w') as archive:
    info = zipfile.ZipInfo('echor')
    info.external_attr = 0o100555 << 16
    with archive.open(info, 'w') as target:
        remaining = int(sys.argv[3])
        while chunk := source.read(min(65536, remaining + 1)):
            if len(chunk) > remaining:
                raise ValueError('native binary exceeds the product member limit')
            target.write(chunk)
            remaining -= len(chunk)
`,
        binary,
        archive,
        String(maxBinary),
      ],
      { timeout: 30_000, maxBuffer: 64 * 1024, stdio: ["ignore", "ignore", "pipe"] },
    );
    if (statSync(archive).size > maxArchive)
      throw new Error("native archive exceeds the fixture body limit");
    return readFileSync(archive);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}
