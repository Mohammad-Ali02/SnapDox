"""Launcher so the web UI can be started without installing the package.

    python serve.py            # from anywhere
    python -m web.app          # equivalent, from the project root
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from web.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
