"""Run ``longpole`` as ``python -m longpole``."""

from __future__ import annotations

import sys

from longpole.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
