# Makes `python -m unittest discover` (no -s) find the tests; CI's `discover -s tests` is unchanged.
# Without the anthropic package installed, an offline stub is used (tests/_anthropic_stub.py); with it, nothing changes.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _anthropic_stub  # noqa: E402

_anthropic_stub.install()
