"""
Helpers automatically injected into every kernel started by the host-side
jupyter_server (see ../jupyter_server_config.py).

These are intentionally minimal and rely only on the standard library plus
IPython, so they are safe to import in any kernel.

Exposed names:
    ipython_history(n=20, search=None) -> list[str]
    grep_source(pattern, path=..., glob=None) -> str
    package_source(module) -> str

Naming convention: leading-underscore globals stay private to the module so
they do not show up in tab-completion when the LLM inspects ``dir()``.
"""

from __future__ import annotations

import fnmatch as _fnmatch
import importlib as _importlib
import inspect as _inspect
import io as _io
import os as _os
import re as _re
import shutil as _shutil
import subprocess as _sp
from typing import List, Optional

try:
    from IPython.core.history import HistoryAccessor as _HistoryAccessor
except Exception:  # pragma: no cover - IPython is always present in our kernels
    _HistoryAccessor = None  # type: ignore[assignment]


_PROJECT_ROOT = _os.environ.get("OPENWEBUI_PROJECT_ROOT") or _os.path.expanduser("~/lego-claw")


def ipython_history(n: int = 20, search: Optional[str] = None) -> List[str]:
    """Return the last *n* IPython input cells.

    When *search* is given, returns up to *n* cells whose source matches the
    SQLite ``LIKE``-style pattern (``*`` and ``?`` wildcards).
    """

    if _HistoryAccessor is None:
        return []
    accessor = _HistoryAccessor()
    if search:
        rows = list(accessor.search(search, n=n))
    else:
        rows = list(accessor.get_tail(n, include_latest=True))
    return [code for _session, _line, code in rows]


_DEFAULT_PRUNE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        ".venv-jupyter",
        ".venv-sherpa",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".cache",
        "data",
        "models",
    }
)


def grep_source(
    pattern: str,
    path: str = _PROJECT_ROOT,
    glob: Optional[str] = None,
    max_count: int = 200,
    timeout: float = 20.0,
) -> str:
    """Search *path* for *pattern* and return ripgrep-style output.

    Prefers ``rg`` when available, then ``grep -R`` (with vendored-dir excludes),
    and finally a pure-Python walker that skips common build/vendor folders.
    """

    rg = _shutil.which("rg")
    if rg:
        cmd = [rg, "--no-heading", "-n", "--max-count", str(max_count)]
        if glob:
            cmd += ["-g", glob]
        cmd += [pattern, path]
        return _sp.run(cmd, capture_output=True, text=True, timeout=timeout).stdout

    grep = _shutil.which("grep")
    if grep:
        cmd = [grep, "-RIn", "--max-count", str(max_count)]
        for vendor in _DEFAULT_PRUNE_DIRS:
            cmd += ["--exclude-dir", vendor]
        if glob:
            cmd += ["--include", glob]
        cmd += ["-e", pattern, path]
        try:
            return _sp.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
        except _sp.TimeoutExpired:
            pass  # fall through to the Python walker

    return _python_grep(pattern, path, glob, max_count)


def _python_grep(
    pattern: str,
    path: str,
    glob: Optional[str],
    max_count: int,
) -> str:
    regex = _re.compile(pattern)
    out = _io.StringIO()
    hits = 0
    for dirpath, dirnames, filenames in _os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in _DEFAULT_PRUNE_DIRS]
        for fname in filenames:
            if glob and not _fnmatch.fnmatch(fname, glob):
                continue
            full = _os.path.join(dirpath, fname)
            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if regex.search(line):
                            out.write(f"{full}:{lineno}:{line.rstrip()}\n")
                            hits += 1
                            if hits >= max_count:
                                return out.getvalue()
            except OSError:
                continue
    return out.getvalue()


def package_source(module: str) -> str:
    """Return the on-disk source path for *module* (importable name)."""

    mod = _importlib.import_module(module)
    src = _inspect.getsourcefile(mod)
    return src or ""


# Expose helpers without polluting the "public" namespace too much.
__all__ = ["ipython_history", "grep_source", "package_source"]
