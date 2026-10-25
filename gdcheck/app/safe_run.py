"""Run a tool module with friendly error messages (used by GD-Tools for every child tool).

    python -m gdcheck.app.safe_run <module> [args ...]

Same as `python -m <module> args`: sys.argv[0] becomes the module, exit codes and sys.exit("text")
pass through unchanged. Only an unexpected crash is turned into the framed explanation of
gdcheck.app.errors (with GDCHECK_FRIENDLY=1) plus a log file for Claude - the tool modules need no
change for that.
"""
from __future__ import annotations

import runpy
import sys

from . import errors


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python -m gdcheck.app.safe_run <module> [args ...]", file=sys.stderr)
        return 2
    module, args = argv[0], argv[1:]
    sys.argv = [module] + args
    tool = errors.tool_name(module)

    def go():
        try:
            runpy.run_module(module, run_name="__main__", alter_sys=True)
        except SystemExit as ex:
            code = ex.code
            if code is None:
                return 0
            if isinstance(code, int):
                return code
            print(code, file=sys.stderr)             # sys.exit("message"): the tool's own text
            return 1
        return 0
    return errors.run(go, tool)


if __name__ == "__main__":
    raise SystemExit(main())
