"""Frozen-app launcher.

Double-clicking the packaged app opens the UI; passing CLI arguments keeps the
command line interface available.
"""

import sys


def main() -> None:
    if len(sys.argv) == 1:
        from autoclip.ui.app import main as ui_main

        ui_main()
        return

    from autoclip.cli import main as cli_main

    raise SystemExit(cli_main())


if __name__ == "__main__":
    main()
