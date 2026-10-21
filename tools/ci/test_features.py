"""Check feature combinations and test feature-bearing workspace packages."""

import json
import subprocess
from typing import TypedDict


class CargoPackage(TypedDict):
    id: str
    name: str
    features: dict[str, list[str]]


class CargoMetadata(TypedDict):
    workspace_members: list[str]
    packages: list[CargoPackage]


def feature_packages(metadata: CargoMetadata) -> list[str]:
    return sorted(
        package["name"]
        for package in metadata["packages"]
        if package["id"] in metadata["workspace_members"] and package["features"]
    )


def main() -> None:
    metadata = json.loads(
        subprocess.check_output(["cargo", "metadata", "--no-deps", "--format-version", "1"])
    )
    for package in feature_packages(metadata):
        subprocess.run(
            ["cargo", "hack", "check", "-p", package, "--all-targets", "--feature-powerset"],
            check=True,
        )
        for features in ("--no-default-features", "--all-features"):
            subprocess.run(
                ["cargo", "nextest", "run", "-p", package, features, "--profile", "ci"], check=True
            )
            subprocess.run(["cargo", "test", "-p", package, features, "--doc"], check=True)


if __name__ == "__main__":
    main()
