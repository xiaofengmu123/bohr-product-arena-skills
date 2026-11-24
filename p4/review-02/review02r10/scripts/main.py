"""main.py -- thin entry wrapper; all logic lives in core_impl.py."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    import core_impl  # noqa: E402

    raise SystemExit(core_impl.main(sys.argv[1:]))
