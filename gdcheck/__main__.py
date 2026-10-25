import sys

from .app import errors
from .app.cli import main

sys.exit(errors.run(main, "build check"))
