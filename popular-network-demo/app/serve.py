"""Thin shim — preserves `python -m app.serve` as an alternate entry point."""
from .main import _main

if __name__ == "__main__":
    _main()
