"""Task text resolution from explicit input, env or the OpenSpec change.

The validation sample names its task in the change directory, so the CLI works
on a new project without a new default string.
"""

from __future__ import annotations

import re
from pathlib import Path

TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
SECTION_RE = r"^##\s+{name}\s*$(.*?)(?=^##\s|\Z)"


def _section(text: str, name: str) -> str:
    match = re.search(SECTION_RE.format(name=name), text, flags=re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _bullets(block: str) -> list[str]:
    items: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            item = stripped[1:].strip()
            if item:
                items.append(item)
    return items


def changes(root: Path) -> list[Path]:
    base = root / "openspec" / "changes"
    if not base.is_dir():
        return []
    return sorted(path for path in base.iterdir() if path.is_dir() and not path.name.startswith("."))


def change_tasks(root: Path, name: str) -> dict[str, object]:
    """Read a change directory into the fields the agent needs."""

    directory = root / "openspec" / "changes" / name
    if not directory.is_dir():
        raise FileNotFoundError(f"openspec change not found: {directory}")

    proposal_path = directory / "proposal.md"
    proposal = proposal_path.read_text(encoding="utf-8") if proposal_path.is_file() else ""
    spec_paths = sorted(directory.glob("specs/**/spec.md"))
    spec_text = "\n\n".join(path.read_text(encoding="utf-8") for path in spec_paths)

    requirements: list[str] = []
    for block in re.split(r"^###\s+Requirement:", spec_text, flags=re.MULTILINE)[1:]:
        first_line = block.strip().splitlines()[0].strip() if block.strip() else ""
        if first_line:
            requirements.append(first_line)

    what_changes = _bullets(_section(proposal, "What Changes"))
    why = _section(proposal, "Why")

    return {
        "name": name,
        "directory": str(directory.relative_to(root).as_posix()),
        "why": why,
        "what_changes": what_changes,
        "requirements": requirements,
        "spec_paths": [path.relative_to(root).as_posix() for path in spec_paths],
    }


def describe(root: Path, name: str) -> str:
    """Build a task sentence from the change instead of a hard-coded default."""

    info = change_tasks(root, name)
    parts: list[str] = []
    changes_list = [str(item) for item in info["what_changes"]]
    requirements = [str(item) for item in info["requirements"]]
    if changes_list:
        parts.append("; ".join(changes_list[:4]))
    elif requirements:
        parts.append("; ".join(requirements[:4]))
    else:
        parts.append(f"implement the {name} change")
    why = str(info["why"]).strip().splitlines()
    if why:
        parts.append(why[0])
    return " — ".join(parts)


def resolve(root: Path, explicit: str | None = None, change: str | None = None) -> tuple[str, str | None]:
    """Return ``(task, change_name)``.

    ``explicit`` wins; otherwise the named change (or the only change in the
    repository) supplies both fields.
    """

    if explicit:
        return explicit, change
    available = changes(root)
    selected = None
    if change:
        selected = root / "openspec" / "changes" / change
        if not selected.is_dir():
            raise FileNotFoundError(f"openspec change not found: {selected}")
    elif len(available) == 1:
        selected = available[0]
    if selected is None:
        if not available:
            raise ValueError(
                "no task given and no openspec change found; pass --task or create "
                "openspec/changes/<change>/"
            )
        names = ", ".join(path.name for path in available)
        raise ValueError(f"multiple openspec changes ({names}); pass --change or --task")
    name = selected.name
    return describe(root, name), name
