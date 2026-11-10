#!/usr/bin/env python3
"""Link this checkout into Blender's add-ons directory (development install).

Blender only re-imports an add-on when it is enabled, so the source folder has
to live somewhere Blender scans. This script points Blender at this checkout
instead of a copied/zipped install, which means every edit here is the code
Blender runs after a reload.

Usage:
    python3 scripts/dev_install.py
    python3 scripts/dev_install.py --blender-version 5.2
    python3 scripts/dev_install.py --scripts-dir ~/blender-dev-scripts
    python3 scripts/dev_install.py --replace        # replace an existing copy
    python3 scripts/dev_install.py --remove

The add-on's preferences are keyed by its folder name. If an existing Anvil
install is found, its folder name is reused so saved preferences survive.

On Windows a directory junction is used when symlinks are unavailable; neither
requires administrator rights.
"""

import argparse
import ast
import os
import re
import subprocess
import sys
import time

DEFAULT_ADDON_NAME = "AnvilPlus"
ADDON_DISPLAY_NAME = "Anvil Level Design"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VERSION_RE = re.compile(r"^\d+\.\d+$")


def config_roots():
    """Candidate Blender config roots, most likely first."""
    roots = []

    flatpak_root = os.path.join(
        os.path.expanduser("~"),
        ".var", "app", "org.blender.Blender", "config", "blender",
    )
    if os.path.isdir(flatpak_root):
        roots.append(("flatpak", flatpak_root))

    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.append((
                "windows",
                os.path.join(appdata, "Blender Foundation", "Blender"),
            ))
    elif sys.platform == "darwin":
        roots.append((
            "macOS",
            os.path.expanduser("~/Library/Application Support/Blender"),
        ))
    else:
        xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        roots.append(("linux", os.path.join(xdg_config, "blender")))

    return roots


def installed_versions(config_root):
    try:
        entries = os.listdir(config_root)
    except OSError:
        return []
    return sorted(
        (name for name in entries if _VERSION_RE.match(name)),
        key=lambda name: tuple(int(part) for part in name.split(".")),
    )


def repo_version():
    """Read bl_info["blender"] from the checkout without importing bpy."""
    init_path = os.path.join(REPO_ROOT, "__init__.py")
    try:
        with open(init_path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=init_path)
    except (OSError, SyntaxError):
        return "5.1"
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "bl_info":
                    try:
                        info = ast.literal_eval(node.value)
                    except (ValueError, SyntaxError):
                        return "5.1"
                    version = info.get("blender", (5, 1))
                    return f"{version[0]}.{version[1]}"
    return "5.1"


def choose_scripts_dir(args):
    """Return the Blender "scripts" directory that contains an addons folder."""
    if args.scripts_dir:
        return os.path.abspath(os.path.expanduser(args.scripts_dir)), "custom"

    roots = []
    if args.config_root:
        roots.append(("custom", os.path.abspath(os.path.expanduser(args.config_root))))
    else:
        roots = config_roots()

    version = args.blender_version
    if version:
        for label, root in roots:
            if os.path.isdir(os.path.join(root, version)):
                return os.path.join(root, version, "scripts"), f"{label} {version}"
        label, root = roots[0]
        return os.path.join(root, version, "scripts"), f"{label} {version}"

    for label, root in roots:
        versions = installed_versions(root)
        if versions:
            chosen = versions[-1]
            return os.path.join(root, chosen, "scripts"), f"{label} {chosen}"

    if not roots:
        raise SystemExit("Could not find a Blender config folder.")
    label, root = roots[0]
    version = repo_version()
    return os.path.join(root, version, "scripts"), f"{label} {version}"


def addons_dir_for(scripts_dir):
    return os.path.join(os.path.abspath(os.path.expanduser(scripts_dir)), "addons")


def looks_like_anvil(folder):
    init_path = os.path.join(folder, "__init__.py")
    if not os.path.isfile(init_path):
        return False
    try:
        with open(init_path, "r", encoding="utf-8", errors="ignore") as handle:
            head = handle.read(4096)
    except OSError:
        return False
    return ADDON_DISPLAY_NAME in head


def find_anvil_installs(addons_dir):
    try:
        entries = os.listdir(addons_dir)
    except OSError:
        return []
    return [
        os.path.join(addons_dir, name)
        for name in entries
        if looks_like_anvil(os.path.join(addons_dir, name))
    ]


def create_link(link_path, target_path):
    if sys.platform.startswith("win"):
        try:
            os.symlink(target_path, link_path, target_is_directory=True)
            return "symlink"
        except OSError:
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", link_path, target_path],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise SystemExit(
                    "Could not create a junction:\n"
                    f"{result.stdout}{result.stderr}\n"
                    "Create it manually with:\n"
                    f'  mklink /J "{link_path}" "{target_path}"'
                )
            return "junction"
    os.symlink(target_path, link_path, target_is_directory=True)
    return "symlink"


def remove_link(link_path):
    if not os.path.lexists(link_path):
        print(f"Nothing to remove at {link_path}")
        return 0
    if os.path.islink(link_path):
        os.unlink(link_path)
        print(f"Removed symlink {link_path}")
        return 0
    if os.path.isdir(link_path):
        # A Windows junction is a directory but not a symlink. Never use
        # shutil.rmtree here: that would delete the checkout it points at.
        try:
            os.rmdir(link_path)
            print(f"Removed junction {link_path}")
            return 0
        except OSError:
            print(
                f"{link_path} is a real directory, not a link. Refusing to "
                "delete it; remove it manually if it is an old install."
            )
            return 1
    os.unlink(link_path)
    print(f"Removed {link_path}")
    return 0


def move_aside(addons_dir, folder):
    """Move an existing install out of the add-ons folder, never delete it."""
    backup_root = os.path.join(os.path.dirname(addons_dir), "addons-backup")
    os.makedirs(backup_root, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = os.path.join(backup_root, f"{os.path.basename(folder)}-{stamp}")
    os.rename(folder, destination)
    print(f"Moved existing install to {destination}")
    return destination


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--blender-version",
        help="Blender version folder to install into (e.g. 5.2). Defaults to the "
        "newest installed version.",
    )
    parser.add_argument(
        "--scripts-dir",
        help="Use a custom Script Directory instead of Blender's user config. "
        "Add this directory in Preferences > File Paths > Script Directories.",
    )
    parser.add_argument(
        "--config-root",
        help="Override the Blender config root (the folder that contains "
        "version folders such as 5.2).",
    )
    parser.add_argument(
        "--name",
        help="Folder name to install as. Defaults to the name of an existing "
        "Anvil install (to keep preferences), otherwise 'AnvilPlus'.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Move an existing Anvil install out of the add-ons folder and "
        "replace it with the link. The old copy is backed up, not deleted.",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove the development link.",
    )
    args = parser.parse_args()

    scripts_dir, version_label = choose_scripts_dir(args)
    addons_dir = addons_dir_for(scripts_dir)

    existing_installs = find_anvil_installs(addons_dir)
    name = args.name
    if not name:
        if len(existing_installs) == 1:
            name = os.path.basename(existing_installs[0])
        else:
            name = DEFAULT_ADDON_NAME
    link_path = os.path.join(addons_dir, name)

    if args.remove:
        return remove_link(link_path)

    os.makedirs(addons_dir, exist_ok=True)

    if os.path.islink(link_path):
        current = os.path.realpath(link_path)
        if current == os.path.realpath(REPO_ROOT):
            print(f"Already linked: {link_path} -> {REPO_ROOT}")
        else:
            print(f"{link_path} is a symlink to {current}, not this checkout.")
            print("Run with --remove first if you want to relink it.")
            return 1
    elif os.path.exists(link_path):
        if not args.replace:
            print(f"{link_path} already exists and is not a link.")
            print(
                "Run again with --replace to move it to an 'addons-backup' "
                "folder and link this checkout instead. Nothing is deleted."
            )
            return 1
        move_aside(addons_dir, link_path)

    if not os.path.exists(link_path):
        kind = create_link(link_path, REPO_ROOT)
        print(f"Created {kind}: {link_path} -> {REPO_ROOT}")

    others = [
        path for path in find_anvil_installs(addons_dir)
        if os.path.abspath(path) != os.path.abspath(link_path)
    ]
    if others:
        print()
        print("Warning: another copy of Anvil was found in the same add-ons folder:")
        for path in others:
            print(f"  {path}")
        print("Disable or remove it so only one copy registers.")

    print()
    print("Next steps:")
    if args.scripts_dir:
        print("  1. Blender > Edit > Preferences > File Paths > Script Directories")
        print(f"     Add: {scripts_dir}")
        print("  2. Restart Blender, then enable 'Anvil Level Design' in Add-ons.")
        print("  3. Open the add-on's preferences and enable 'Auto Reload'")
        print("     (Anvil Settings > Developer).")
    else:
        print(f"  1. Restart Blender ({version_label}) and enable 'Anvil Level Design'")
        print("     in Edit > Preferences > Add-ons.")
        print("  2. Open the add-on's preferences and enable 'Auto Reload'")
        print("     (Anvil Settings > Developer).")
    print()
    print("From then on, saving any .py file in this folder reloads the add-on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
