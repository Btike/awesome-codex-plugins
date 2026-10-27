"""JSON artifact writing and explicit retired bundle cleanup helpers.

Kept separate from the marketplace generator to preserve the 500-line source limit.
"""

from pathlib import Path
import json
import shutil

RETIRED_BUNDLE_IDENTITIES = {("samuelbushi", "uizze"), ("waynehamadi", "datacircle-plugin")}

def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def prune_unlisted_bundles(root: Path, plugins: list[dict[str, str]]) -> list[str]:
    """Remove only explicitly reviewed retirements that are absent from README.

    Omission from the parsed catalog never authorizes another retirement.
    Keep listed repositories even if this run cannot fetch them, and preflight
    the two-level tree for symlinks before deleting any approved bundle.
    """
    if not plugins:
        raise ValueError("Refusing bundle cleanup from an empty parsed README; explicit maintainer retirement is required")
    retained = {(plugin["owner"].casefold(), plugin["repo"].casefold()) for plugin in plugins}
    removed: list[str] = []
    if root.is_symlink():
        raise ValueError("Generated plugins root must not be a symlink")
    if not root.exists():
        return removed
    # Preflight the entire tree before retiring anything. A later invalid
    # directory must not leave an earlier bundle partially retired.
    retired: list[Path] = []
    for owner in sorted(root.iterdir()):
        if owner.is_symlink():
            raise ValueError(f"Generated owner directory must not be a symlink: {owner}")
        if not owner.is_dir():
            continue
        for repo in sorted(owner.iterdir()):
            if repo.is_symlink():
                raise ValueError(f"Generated repo directory must not be a symlink: {repo}")
            identity = (owner.name.casefold(), repo.name.casefold())
            if repo.is_dir() and identity in RETIRED_BUNDLE_IDENTITIES and identity not in retained:
                retired.append(repo)
    for repo in retired:
        shutil.rmtree(repo)
        removed.append(f"{repo.parent.name}/{repo.name}")
    return removed
