"""Frozen-app launcher.

Double-clicking the packaged app opens the UI; passing CLI arguments keeps the
command line interface available.
"""

import os
import sys


def _ensure_stdout() -> None:
    """Windowed builds have no stdout; attach to the calling console if possible."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    if sys.platform == "win32":
        try:
            import ctypes

            if ctypes.windll.kernel32.AttachConsole(-1):
                sys.stdout = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
                sys.stderr = sys.stdout
                return
        except Exception:
            pass
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    sys.stderr = sys.stdout


def main() -> None:
    if len(sys.argv) == 1:
        from autoclip.ui.app import main as ui_main

        ui_main()
        return

    _ensure_stdout()
    from autoclip.cli import main as cli_main

    raise SystemExit(cli_main())


if __name__ == "__main__":
    main()
