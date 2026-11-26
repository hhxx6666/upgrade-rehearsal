"""Generate a fresh backup, media tree, and matching configuration with real containers."""

import sys

from upgrade_rehearsal.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["seed", *sys.argv[1:]]))
