"""Runtime hook: make a double-clicked bundle open the app.

Finder launches ``Contents/MacOS/sniff`` with no arguments, and the plain ``sniff``
command line answers that with usage text and exit code 2. In a windowed bundle
there is no console, so the whole thing looks like a double-click that did
nothing. Inside a frozen bundle, "no arguments" can only mean "start the app",
because anyone typing a command would have named a subcommand.
"""

import sys

if getattr(sys, "frozen", False) and len(sys.argv) == 1:
    sys.argv.append("app")
