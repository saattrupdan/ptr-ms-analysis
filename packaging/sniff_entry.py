"""Entry point for the packaged app.

PyInstaller needs a script to freeze; the installed console script does the same
thing through ``[project.scripts]``. Keeping this in ``packaging/`` means the frozen
build and the checkout CLI take the same path into ``analyze:main``.
"""

from sniff.analyze import main

if __name__ == "__main__":
    raise SystemExit(main())
