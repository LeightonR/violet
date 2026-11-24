import sys

if sys.platform == "win32":
    from .app import run
else:
    from .portable import run


if __name__ == "__main__":
    raise SystemExit(run())
