"""Extract Svelte scripts, preserving line offsets for diagnostics."""

import re


def script(path: str, text: str) -> str:
    if not path.endswith(".svelte"):
        return text
    # Keep script offsets so diagnostics still point into the original component.
    output = ["\n" if c == "\n" else " " for c in text]
    for match in re.finditer(r"<script\b[^>]*>(.*?)</script\s*>", text, re.DOTALL | re.IGNORECASE):
        output[match.start(1) : match.end(1)] = match.group(1)
    return "".join(output)
