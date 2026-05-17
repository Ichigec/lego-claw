"""
jupyter_server config for the OpenWebUI <-> host Jupyter integration.

Loaded by jupyter-host-start.sh via `jupyter server --config=...`. Listens on
loopback only (127.0.0.1); Docker containers reach it through host-gateway via
the extra_hosts entry in compose.openwebui.yml.

Auth: token-only. JUPYTER_TOKEN must be set in the environment before launch.

Root dir: by default the Jupyter root_dir is $HOME/agent_dev — the
sandbox that the OpenWebUI code-agent (fsbox + this kernel) is allowed to
write to. Override with JUPYTER_ROOT_DIR if you want to point the kernel at
a different working tree (e.g. back to the main project for a one-off data
crunch, or at a per-task subdirectory).
"""

from __future__ import annotations

import os
from pathlib import Path

c = get_config()  # type: ignore[name-defined]  # noqa: F821 -- traitlets magic

DEFAULT_ROOT_DIR = os.path.expanduser("~/agent_dev")
ROOT_DIR = Path(os.environ.get("JUPYTER_ROOT_DIR") or DEFAULT_ROOT_DIR).resolve()
STARTUP_DIR = Path(__file__).resolve().parent / "startup"

c.ServerApp.ip = os.environ.get("JUPYTER_HOST", "127.0.0.1")
c.ServerApp.port = int(os.environ.get("JUPYTER_PORT", "8888"))
c.ServerApp.open_browser = False
c.ServerApp.allow_remote_access = True
c.ServerApp.allow_origin = "*"
c.ServerApp.disable_check_xsrf = True
c.ServerApp.password = ""
c.ServerApp.token = os.environ.get("JUPYTER_TOKEN", "")
c.ServerApp.root_dir = str(ROOT_DIR)
c.ServerApp.terminado_settings = {"shell_command": ["/bin/bash"]}

c.MappingKernelManager.cull_idle_timeout = 1800
c.MappingKernelManager.cull_interval = 300
c.MappingKernelManager.cull_connected = False

c.ServerApp.tornado_settings = {
    "headers": {
        "Content-Security-Policy": "frame-ancestors 'self' http://localhost:* http://127.0.0.1:*",
    },
}

# Inject project-level helpers into every kernel started under this server.
# This keeps the LLM-facing helpers in one place even if the user has their own
# ~/.ipython/profile_default/startup/ files.
c.IPKernelApp.exec_lines = [
    f"%run {STARTUP_DIR / '00_helpers.py'}",
]
