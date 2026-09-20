"""Verify the public methodology bundle without data or network access."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures: list[str] = []
    for relative, expected in payload["files"].items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing:{relative}")
        elif sha256(path) != expected:
            failures.append(f"hash:{relative}")
    extras = sorted(
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in ROOT.rglob("*")
        if path.is_file() and path.name != MANIFEST.name
        and str(path.relative_to(ROOT)).replace("\\", "/") not in payload["files"]
    )
    if extras:
        failures.extend(f"unmanifested:{item}" for item in extras)
    if failures:
        raise SystemExit("PACKAGE_VERIFY_FAILED\n" + "\n".join(failures))
    print(f"PACKAGE_VERIFY_OK files={len(payload['files'])}")


if __name__ == "__main__":
    main()
