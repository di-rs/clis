"""Check nonempty package READMEs and their root index entries."""

from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from cli_check.diagnostics import Diagnostic
from cli_check.workspace import Package


def local_links(path: Path) -> list[tuple[str, Path]]:
    links: list[tuple[str, Path]] = []
    for token in MarkdownIt().parse(path.read_text()):
        for child in token.children or []:
            value = (
                child.attrGet("href")
                if child.type == "link_open"
                else child.attrGet("src")
                if child.type == "image"
                else None
            )
            if not isinstance(value, str):
                continue
            url = urlsplit(value)
            if not url.scheme and not url.netloc and url.path:
                links.append((value, (path.parent / unquote(url.path)).resolve()))
    return links


def check(root: Path, packages: list[Package]) -> list[Diagnostic]:
    clis = [package for package in packages if package.is_cli]
    if not clis:
        return []
    errors: list[Diagnostic] = []
    readmes = [root / "README.md", *(package.directory / "README.md" for package in clis)]
    root_links: set[Path] = set()
    for readme in dict.fromkeys(readmes):
        relative = readme.relative_to(root).as_posix()
        if not readme.is_file() or not readme.read_bytes().strip():
            errors.append(Diagnostic(relative, "docs", "readme", "requires a nonempty README.md"))
            continue
        if readme == root / "README.md":
            root_links.update(target for _, target in local_links(readme))
    for package in clis:
        if package.directory == root:
            continue
        readme = package.directory / "README.md"
        if readme.resolve() not in root_links:
            errors.append(
                Diagnostic(
                    "README.md",
                    "docs",
                    "index",
                    f"root README must link {readme.relative_to(root).as_posix()}",
                )
            )
    return errors
