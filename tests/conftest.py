"""
Test-session setup.

DATABASE_URL is pinned to a throwaway SQLite file here, before any app module
is imported. The engine is initialised once per process from settings, so a
test module that sets the env var after that point silently keeps using
whatever database was already connected - which previously sent the feedback
tests at the real database.
"""
import os
import tempfile

os.environ.setdefault("AUTH_MODE", "local")

if "DATABASE_URL" not in os.environ or not os.environ["DATABASE_URL"].startswith("sqlite"):
    _fd, _path = tempfile.mkstemp(prefix="analytrix-tests-", suffix=".db")
    os.close(_fd)
    os.environ["DATABASE_URL"] = f"sqlite:///{_path}"
    # Windows keeps a lock while a connection is open, so a failure here is
    # expected and must not print a traceback over the test results.
    import atexit

    def _cleanup() -> None:
        try:
            if os.path.exists(_path):
                os.remove(_path)
        except OSError:
            pass

    atexit.register(_cleanup)
