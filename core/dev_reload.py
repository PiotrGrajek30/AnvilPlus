"""Developer hot reload.

Watches the addon source tree and re-registers the addon whenever a Python
file changes, so edits can be picked up without restarting Blender or
reinstalling the add-on.

This is a development-only convenience. It is disabled by default and can be
enabled either with the "Auto Reload" toggle in the add-on preferences or with
the ``ANVIL_DEV_AUTORELOAD=1`` environment variable.
"""

import importlib.util
import os
import shutil
import sys
import time
import traceback

import bpy

_PACKAGE = (__package__ or __name__).rsplit(".", 1)[0]
_SOURCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WATCH_DIR = os.path.realpath(_SOURCE_DIR)

_POLL_INTERVAL = 0.5
_SETTLE_SECONDS = 0.5

_SKIP_DIRS = frozenset({
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
    "tests",
    "test_outputs",
    "site",
})

# Modal operators that do not prevent a safe reload.
_PASSIVE_MODAL_OPERATOR_IDS = frozenset({
    "LEVELDESIGN_OT_prefab_browser_interaction",
    "LEVELDESIGN_OT_texture_browser_interaction",
})

_last_snapshot = None
_pending_change_at = None
_timer_running = False
_reloading = False


def _env_override():
    value = os.environ.get("ANVIL_DEV_AUTORELOAD")
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_enabled():
    override = _env_override()
    if override is not None:
        return override
    try:
        addon = bpy.context.preferences.addons.get(_PACKAGE)
    except Exception:
        return False
    if addon is None:
        return False
    try:
        prefs = addon.preferences
    except Exception:
        return False
    return bool(getattr(prefs, "dev_auto_reload", False))


def _snapshot():
    """Map every watched .py file to its (mtime, size)."""
    snapshot = {}
    stack = [_WATCH_DIR]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    name = entry.name
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if name in _SKIP_DIRS or name.startswith("."):
                                continue
                            stack.append(entry.path)
                        elif name.endswith(".py"):
                            stat = entry.stat(follow_symlinks=False)
                            snapshot[entry.path] = (stat.st_mtime_ns, stat.st_size)
                    except OSError:
                        continue
        except OSError:
            continue
    return snapshot


def _render_running():
    try:
        return bpy.app.is_job_running("RENDER")
    except Exception:
        return False


def _blocking_modal_active():
    try:
        from ..handlers.modal_state import blocking_modal_operator_ids
    except Exception:
        return False
    try:
        windows = bpy.context.window_manager.windows
    except Exception:
        return False
    for window in windows:
        if blocking_modal_operator_ids(window):
            return True
    return False


def _tick():
    global _last_snapshot, _pending_change_at, _timer_running

    if not _timer_running:
        return None

    # Never pull the rug out from under a modal tool or a render.
    if _render_running() or _blocking_modal_active():
        return _POLL_INTERVAL

    try:
        current = _snapshot()
    except Exception:
        traceback.print_exc()
        return _POLL_INTERVAL

    now = time.monotonic()
    if _last_snapshot is None:
        _last_snapshot = current
        return _POLL_INTERVAL

    if current != _last_snapshot:
        _last_snapshot = current
        _pending_change_at = now
        return _POLL_INTERVAL

    # Wait for the file writes to settle so a multi-file edit only reloads once.
    if _pending_change_at is None or (now - _pending_change_at) < _SETTLE_SECONDS:
        return _POLL_INTERVAL

    _pending_change_at = None
    print(
        f"Anvil Level Design: Source change detected, reloading {_PACKAGE}...",
        flush=True,
    )
    request_reload()
    return None


def start_watcher(force=False):
    global _timer_running, _last_snapshot, _pending_change_at

    if _timer_running:
        return
    if not force and not is_enabled():
        return

    _last_snapshot = None
    _pending_change_at = None
    try:
        if not bpy.app.timers.is_registered(_tick):
            bpy.app.timers.register(
                _tick,
                first_interval=_POLL_INTERVAL,
                persistent=True,
            )
        _timer_running = True
        print(
            f"Anvil Level Design: Auto reload watching {_WATCH_DIR}",
            flush=True,
        )
    except Exception:
        traceback.print_exc()


def stop_watcher():
    global _timer_running

    _timer_running = False
    try:
        if bpy.app.timers.is_registered(_tick):
            bpy.app.timers.unregister(_tick)
    except Exception:
        pass


def request_reload():
    """Queue a reload so it runs after the current operator/timer finishes."""
    try:
        if bpy.app.timers.is_registered(_deferred_reload):
            return
        bpy.app.timers.register(_deferred_reload, first_interval=0.01)
    except Exception:
        traceback.print_exc()


def _deferred_reload():
    global _last_snapshot

    was_watching = _timer_running
    stop_watcher()
    if not reload_addon():
        # Keep watching so that saving a fix triggers another attempt.
        _last_snapshot = None
        if was_watching:
            start_watcher(force=True)
    return None


def _clear_bytecode():
    """Delete stale .pyc files so a reload can never pick up old code."""
    for root, dirs, _files in os.walk(_WATCH_DIR):
        if "__pycache__" in dirs:
            shutil.rmtree(os.path.join(root, "__pycache__"), ignore_errors=True)
            dirs.remove("__pycache__")


def _unregister_old():
    old_module = sys.modules.get(_PACKAGE)
    if old_module is None:
        return
    unregister = getattr(old_module, "unregister", None)
    if callable(unregister):
        try:
            unregister()
        except Exception:
            traceback.print_exc()


def _purge_modules():
    for name in [
        name for name in sys.modules
        if name == _PACKAGE or name.startswith(_PACKAGE + ".")
    ]:
        sys.modules.pop(name, None)


def _import_package():
    init_path = os.path.join(_SOURCE_DIR, "__init__.py")
    spec = importlib.util.spec_from_file_location(
        _PACKAGE,
        init_path,
        submodule_search_locations=[_SOURCE_DIR],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create an import spec for {init_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PACKAGE] = module
    spec.loader.exec_module(module)
    return module


def reload_addon():
    """Unregister, re-import and register the add-on from disk."""
    global _reloading

    if _reloading:
        return False
    _reloading = True
    try:
        _unregister_old()
        _purge_modules()
        _clear_bytecode()
        try:
            module = _import_package()
            module.register()
        except Exception:
            traceback.print_exc()
            print(
                "Anvil Level Design: Reload failed. Fix the error and save a "
                "Python file to retry.",
                flush=True,
            )
            return False
        module.__addon_enabled__ = True
        print(f"Anvil Level Design: Reloaded {_PACKAGE}", flush=True)
        return True
    finally:
        _reloading = False
