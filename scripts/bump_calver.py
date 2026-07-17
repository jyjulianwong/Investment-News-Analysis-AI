#!/usr/bin/env python3
"""Bump the calendar version, update lambda/pyproject.toml, and create a git tag.

Usage (run from repo root in CI):
    python3 scripts/bump_calver.py

Outputs (written to $GITHUB_OUTPUT if set):
    version=YYYY.MM.DD.MINOR
    tag=vYYYY.MM.DD.MINOR
"""

import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, check=True, text=True, capture_output=True, **kwargs)
    return result


def main() -> None:
    today = datetime.now(tz=timezone.utc).strftime("%Y.%m.%d")

    existing_tags = run(["git", "tag", "--list", f"v{today}.*"]).stdout.splitlines()
    existing_tags = [t.strip() for t in existing_tags if t.strip()]

    if existing_tags:
        minors = [int(t.rsplit(".", 1)[-1]) for t in existing_tags]
        minor = max(minors) + 1
    else:
        minor = 0

    version = f"{today}.{minor}"
    tag = f"v{version}"

    pyproject = Path("lambda/pyproject.toml")
    original = pyproject.read_text()
    updated = re.sub(
        r'^version = "[^"]+"', f'version = "{version}"', original, count=1, flags=re.MULTILINE
    )
    if updated == original:
        print(f"ERROR: could not find version field in {pyproject}", file=sys.stderr)
        sys.exit(1)
    pyproject.write_text(updated)

    run(["git", "config", "user.name", "github-actions[bot]"])
    run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    run(["git", "add", str(pyproject)])
    run(["git", "commit", "-m", f"chore: bump version to {version}"])
    run(["git", "tag", "-a", tag, "-m", tag])

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"version={version}\n")
            f.write(f"tag={tag}\n")
    else:
        print(f"version={version}")
        print(f"tag={tag}")

    print(f"Bumped to {tag}", file=sys.stderr)


if __name__ == "__main__":
    main()
