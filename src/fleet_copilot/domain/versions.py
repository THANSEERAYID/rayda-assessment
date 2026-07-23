"""Platform-aware OS version parsing and comparison.

The dataset mixes two incompatible version grammars:

    darwin : "13.6", "14.5", "15.3", "15.4"       -> dotted numeric
    win32  : "10 22H2", "11 22H2", "11 23H2"      -> major + "YYHN" release tag

Comparing them numerically would be nonsense ("Windows 11" is not newer than
"macOS 15"), so ordering is only ever defined *within* a platform. A question
like "devices running an OS older than macOS 15" therefore resolves to
"platform == darwin AND version < (15,)" and must not sweep Windows devices in.
"""
from __future__ import annotations

import re

_WIN_RELEASE = re.compile(r"^(\d+)\s*H([12])$", re.IGNORECASE)

# Aliases an administrator might type, mapped to the platform key in the data.
PLATFORM_ALIASES = {
    "macos": "darwin",
    "mac os": "darwin",
    "osx": "darwin",
    "mac": "darwin",
    "darwin": "darwin",
    "windows": "win32",
    "win": "win32",
    "win32": "win32",
}


def normalise_platform(name: str) -> str | None:
    """Map a human platform name onto the dataset's ``platform`` value."""
    return PLATFORM_ALIASES.get(name.strip().lower())


def parse_version(platform: str, version: str) -> tuple[int, ...]:
    """Parse an OS version string into a comparable tuple.

    Tuples are only comparable against others from the same platform.
    """
    version = version.strip()
    if platform == "win32":
        return _parse_windows(version)
    return _parse_dotted(version)


def _parse_dotted(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in version.split("."):
        digits = re.match(r"\d+", chunk.strip())
        if not digits:
            break
        parts.append(int(digits.group()))
    return tuple(parts) or (0,)


def _parse_windows(version: str) -> tuple[int, ...]:
    """``"11 23H2"`` -> ``(11, 23, 2)``.

    The release tag encodes year and half-year, so ``23H2`` sorts after ``22H2``
    and ``11 22H2`` sorts after ``10 22H2``.
    """
    tokens = version.split()
    if not tokens:
        return (0,)
    major = _parse_dotted(tokens[0])[0]
    if len(tokens) == 1:
        return (major,)
    tag = _WIN_RELEASE.match(tokens[1])
    if tag:
        return (major, int(tag.group(1)), int(tag.group(2)))
    return (major, *_parse_dotted(tokens[1]))


def is_older_than(platform: str, version: str, target: str) -> bool:
    """True when ``version`` precedes ``target`` on the same platform.

    Comparison is prefix-aligned so a coarse target works as expected:
    ``is_older_than("darwin", "14.5", "15")`` is True because ``(14,)`` < ``(15,)``.
    """
    parsed = parse_version(platform, version)
    wanted = parse_version(platform, target)
    depth = min(len(parsed), len(wanted))
    return parsed[:depth] < wanted[:depth]


def compare(platform: str, left: str, right: str) -> int:
    """Return -1/0/1 for ``left`` versus ``right`` on the same platform."""
    a = parse_version(platform, left)
    b = parse_version(platform, right)
    depth = min(len(a), len(b))
    a, b = a[:depth], b[:depth]
    return (a > b) - (a < b)
