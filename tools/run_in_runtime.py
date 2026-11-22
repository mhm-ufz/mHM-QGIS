"""Run a command inside the installed private QGIS runtime (CI helper).

    python tools/run_in_runtime.py python -m pytest tests -q
"""

import subprocess
import sys

import mhm_qgis_runtime as runtime


def main(argv=None) -> int:
    root = runtime.runtime_root()
    if runtime.active_runtime(root) is None:
        print("No private runtime is installed; run `mhm-qgis-runtime install` first.", file=sys.stderr)
        return 1
    command = [str(part) for part in runtime._pixi_run(root)] + list(sys.argv[1:] if argv is None else argv)
    return subprocess.run(command, env=runtime._environment(), check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
