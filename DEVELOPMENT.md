# Development Setup

Anvil can be developed directly from this checkout: no zip, no reinstall, and
no Blender restart for normal Python edits.

## 1. Dev install (one time)

Blender has to find the add-on through one of its script paths. Point a
directory in Blender's add-ons folder at this checkout instead of copying it.

```sh
python3 scripts/dev_install.py --replace
```

The script:

- finds your newest Blender config folder (native, or the Flatpak one at
  `~/.var/app/org.blender.Blender/config/blender`),
- reuses the folder name of an existing Anvil install so saved preferences
  survive (`anvil-level-design-main` in a normal zip install),
- moves the old copy to `<version>/scripts/addons-backup/` (never deletes it),
- creates `<version>/scripts/addons/<name>` as a link to this repository.

On Windows a directory junction is used when symlinks are unavailable; neither
needs administrator rights.

Other options:

```sh
python3 scripts/dev_install.py --blender-version 5.2
python3 scripts/dev_install.py --name AnvilPlus
python3 scripts/dev_install.py --scripts-dir ~/blender-dev-scripts
python3 scripts/dev_install.py --remove
```

`--scripts-dir` is for Blender's documented custom Script Directory: add that
folder in **Edit > Preferences > File Paths > Script Directories**, then
restart Blender.

## 2. Enable the add-on

Start Blender, open **Edit > Preferences > Add-ons**, and enable
**Anvil Level Design**. If an older copy is installed elsewhere, disable or
remove it so two copies do not register at once; `dev_install.py` warns if it
finds one.

## 3. Enable Auto Reload

In the add-on preferences (**Anvil Settings > Developer**) tick **Auto Reload**.

That is the whole loop: edit a `.py` file, save, and the add-on unregisters and
registers itself with the new code. Changes to `__init__.py`, `bl_info`, new
files, and deleted files are all picked up.

You can also trigger it manually:

- **Anvil Settings > Developer > Reload Addon**
- `bpy.ops.leveldesign.reload_addon()` from the Python console
- `ANVIL_DEV_AUTORELOAD=1 blender` enables the watcher for that session without
  touching preferences (`ANVIL_DEV_AUTORELOAD=0` forces it off)

## How it works

`core/dev_reload.py` polls the source tree every 0.5 s, waits for writes to
settle, then:

1. calls the add-on's `unregister()`,
2. removes every module belonging to the add-on from `sys.modules` (and stale
   `__pycache__` files),
3. re-imports `__init__.py` and calls `register()`.

The reload is postponed while a render or a blocking modal operator is running,
so it will not interrupt a tool mid-drag. It also retries after a failed reload:
fix the syntax error, save, and it reloads again.

## Limitations

- Changes to compiled extensions, installed packages, or Blender itself still
  need a restart.
- If `register()` raises, the add-on may be partially registered. Check the
  console/terminal for the traceback; the next save retries.
- Reloading cannot undo keymap edits made by a previous version. If hotkeys act
  strangely, restart Blender once.
- Auto Reload is a development feature. It is off by default and should stay
  off in released installs.
