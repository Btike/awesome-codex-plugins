"""JSON artifact writing and explicit retired bundle cleanup helpers.

Kept separate from the marketplace generator to preserve the 500-line source limit.
"""

from pathlib import Path, PurePosixPath
import json
import shutil
import stat
import zipfile

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


def verify_archive_members(
    archive: zipfile.ZipFile, plugin_root: PurePosixPath, selected_paths: set[str]
) -> None:
    """Check selected ZIP payloads before replacing a published mirror."""
    # Duplicate archive names are ambiguous: ZipFile.getinfo/read select the
    # last occurrence, but a different ZIP consumer may install the first.
    # Reject only selected members, before the existing bundle is removed.
    selected_archive_names = {
        plugin_root.joinpath(PurePosixPath(path)).as_posix()
        for path in selected_paths
    }
    seen_selected: set[str] = set()
    for info in archive.infolist():
        if info.filename in selected_archive_names:
            if info.filename in seen_selected:
                raise ValueError(f"Duplicate selected ZIP member: {info.filename!r}")
            seen_selected.add(info.filename)
    for relative_path in selected_paths:
        archive_name = plugin_root.joinpath(PurePosixPath(relative_path)).as_posix()
        info = archive.getinfo(archive_name)
        # Git archives can encode symlinks as their target text. Installing
        # that payload as a regular file breaks runtime entry points, while
        # following it may escape the reviewed source bundle.
        if info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError(f"Selected source file is a symlink: {relative_path!r}")
        with archive.open(archive_name) as member:
            while member.read(1024 * 1024):
                pass


def require_real_mirror_parents(root: Path, destination: Path) -> None:
    """Do not replace a mirrored bundle through a symlinked output directory."""
    if root.is_symlink():
        raise ValueError("Generated plugins root must not be a symlink")
    try:
        parts = destination.relative_to(root).parts
    except ValueError as exc:
        raise ValueError("Mirror destination is outside generated plugins root") from exc
    current = root
    for part in parts:
        if part in {".", ".."}:
            raise ValueError("Mirrored bundle path contains traversal")
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Mirrored bundle path must not follow a symlink: {current}")
