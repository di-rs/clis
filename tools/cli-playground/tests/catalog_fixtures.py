"""Small hand-authored public interface, independent of the Rust exporter."""

import hashlib

from cli_playground.catalog import Argument, Arity, Catalog, CliCatalog, CliSchema, Command

from .helpers import elf_binary


def schema(name: str = "echor", about: str = "Write text") -> CliSchema:
    return CliSchema(
        schema_version=1,
        command=Command(
            name=name,
            about=about,
            long_about=None,
            aliases=[],
            usage=f"Usage: {name} [OPTIONS]",
            usage_override=None,
            args=[
                Argument(
                    id="verbose",
                    short="v",
                    long="verbose",
                    aliases=["--detail"],
                    help="Show details",
                    long_help="Show additional diagnostic details.",
                    value_names=[],
                    default_values=["0"],
                    possible_values=[],
                    required=False,
                    index=None,
                    num_args=Arity(min=0, max=0),
                    action="count",
                    global_=True,
                    owner=[],
                )
            ],
            subcommands=[],
        ),
    )


def catalog(sha: str = "a" * 40, about: str = "Write text") -> Catalog:
    return Catalog(
        schema_version=1,
        head_sha=sha,
        base_sha="b" * 40,
        clis=[
            CliCatalog(
                name="echor",
                binary_sha256=hashlib.sha256(elf_binary()).hexdigest(),
                head=schema(about=about),
                base=schema(about="Old description"),
                comparison="available",
            )
        ],
    )
