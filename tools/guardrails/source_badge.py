"""Count nonblank maintained source lines for a display badge."""

from pathlib import Path

from .changes import contents, files, source


def badge(output: Path) -> None:
    count = sum(
        sum(bool(line.strip()) for line in contents("HEAD", path).splitlines())
        for path in files("HEAD")
        if source(path)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="20" role="img" '
        f'aria-label="source nonblank lines: {count}"><rect width="220" height="20" '
        'rx="3" fill="#374151"/><text x="110" y="14" fill="white" text-anchor="middle" '
        f'font-family="Verdana,sans-serif" font-size="11">source nonblank lines: {count}</text></svg>\n'
    )


if __name__ == "__main__":
    badge(Path("badge/loc.svg"))
