"""Allow ``python -m gcgauge`` as an alias for the ``gcgauge`` entry point."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
