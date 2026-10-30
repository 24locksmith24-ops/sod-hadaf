"""Offline stand-in for the anthropic package, installed ONLY when the real package is not importable (R1526 review: the tests
must run without the SDK). CI installs requirements.txt, so there the real, pinned SDK is used and this file does nothing.
The stub accepts temperature= like the pinned 0.125.0 and never makes a network call."""
import sys
import types


def install():
    try:
        import anthropic  # noqa: F401
        return False
    except ImportError:
        pass

    class _Messages:
        def create(self, *, model, max_tokens, messages, system=None, temperature=None):
            raise RuntimeError("anthropic stub: no network in tests")

    class Anthropic:
        def __init__(self, api_key=None):
            self.messages = _Messages()

    sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=Anthropic, __version__="0-test-stub")
    return True
