"""Target of the OS URL-scheme handlers on Windows/Linux.

The OS launches ``python -m nexus_mcp.auth.callback --out <file> <url>`` when
the browser opens ``nexusmcp://...`` or ``ltgopenlmsapp://...``; we write the
link to the file ``nexus-mcp login`` is polling. (macOS uses an AppleScript
applet instead; see ``auth/client.py``.)
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out: Path | None = None
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 < len(argv):
            out = Path(argv[i + 1])
            del argv[i : i + 2]
    if out is None:
        from ..config import Settings
        from .client import CALLBACK_FILENAME

        out = Settings.from_env().config_dir / CALLBACK_FILENAME
    link = next((a.strip() for a in argv if "token=" in a), None)
    if not link:
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(link, "utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
