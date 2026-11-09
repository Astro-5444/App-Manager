from flask import Flask, jsonify, request, send_from_directory
import subprocess
import os
import base64
import json
import shutil
import uuid
import shlex
import threading

app = Flask(__name__, static_folder="static")

# ── Config ──────────────────────────────────────────────────────────────────
CUSTOM_METADATA_FILE = os.path.expanduser("~/.config/app_manager_meta.json")
ICONS_DIR            = os.path.expanduser("~/.config/app_manager_icons")
USER_DESKTOP_DIR     = os.path.expanduser("~/.local/share/applications")

# Serializes meta + .desktop write transactions across Flask's worker threads
# (Save fires rename/set_icon/set_visibility in parallel; without this they
# clobber each other's temp files and lose each other's key changes).
SAVE_LOCK = threading.RLock()

# Packages that must never be removed through the uninstall endpoint. A bad
# dpkg lookup (or an app_id that happens to match a meta-package) could
# otherwise purge the whole desktop environment.
PROTECTED_PACKAGES = {
    "zorin-os-desktop",
    "zorin-desktop-session",
    "gnome-shell",
    "gdm3",
    "lightdm",
    "nautilus",
    "ubuntu-desktop",
    "zorin-os-core",
    "xorg",
    "xdg-desktop-portal",
    "xdg-desktop-portal-gnome",
    "xdg-desktop-portal-gtk",
}

# ── Metadata helpers ─────────────────────────────────────────────────────────
def load_meta():
    try:
        if os.path.exists(CUSTOM_METADATA_FILE):
            with open(CUSTOM_METADATA_FILE, "r") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        # Corrupt/empty file must not break every API call
        return {}
    return {}

def save_meta(meta):
    with SAVE_LOCK:
        os.makedirs(os.path.dirname(CUSTOM_METADATA_FILE), exist_ok=True)
        # Unique temp name so concurrent writers can never clobber each other
        tmp = f"{CUSTOM_METADATA_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w") as f:
            json.dump(meta, f, indent=2)
        os.replace(tmp, CUSTOM_METADATA_FILE)

# ── App discovery ─────────────────────────────────────────────────────────────
def get_desktop_apps():
    """Scan .desktop files. Later dirs override earlier ones, so the user's
    ~/.local/share/applications copies win over system files (XDG precedence)
    — that matters because we write name/icon/visibility changes there."""
    dirs = [
        "/usr/share/applications",
        "/var/lib/flatpak/exports/share/applications",
        os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
        USER_DESKTOP_DIR,
    ]
    apps = {}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if not fname.endswith(".desktop"):
                continue
            path = os.path.join(d, fname)
            info = parse_desktop_file(path, fname)
            if info and info.get("Name"):
                apps[fname] = info
    return list(apps.values())

def parse_desktop_file(path, fname):
    info = {"desktop_file": path, "id": fname.replace(".desktop", "")}
    try:
        with open(path, "r", errors="replace") as f:
            in_desktop = False
            for line in f:
                line = line.strip()
                if line == "[Desktop Entry]":
                    in_desktop = True
                    continue
                if line.startswith("[") and line != "[Desktop Entry]":
                    if in_desktop:
                        break
                if not in_desktop:
                    continue
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    k = k.strip()
                    if k in ("Name", "Icon", "Comment", "Categories", "Exec", "NoDisplay", "Hidden", "Type"):
                        info[k] = v.strip()
    except Exception:
        return None
    info["system_hidden"] = (
        info.get("NoDisplay", "false").lower() == "true"
        or info.get("Hidden", "false").lower() == "true"
    )
    return info

def resolve_icon(icon_name):
    """Try to find an icon file from a name. Returns base64 data URI or None."""
    if not icon_name:
        return None
    # Already an absolute path
    if os.path.isabs(icon_name) and os.path.exists(icon_name):
        return icon_to_b64(icon_name)
    # Search common icon dirs
    search_dirs = [
        "/usr/share/pixmaps",
        "/usr/share/icons/hicolor/128x128/apps",
        "/usr/share/icons/hicolor/64x64/apps",
        "/usr/share/icons/hicolor/48x48/apps",
        "/usr/share/icons/hicolor/scalable/apps",
        "/usr/share/icons/Adwaita/48x48/apps",
        "/usr/share/icons/gnome/48x48/apps",
    ]
    exts = ["", ".png", ".svg", ".xpm"]
    for d in search_dirs:
        for ext in exts:
            candidate = os.path.join(d, icon_name + ext)
            if os.path.exists(candidate):
                return icon_to_b64(candidate)
    return None

def icon_to_b64(path):
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        ext = path.rsplit(".", 1)[-1].lower()
        mime = {"png": "image/png", "svg": "image/svg+xml",
                "xpm": "image/x-xpixmap", "jpg": "image/jpeg"}.get(ext, "image/png")
        return f"data:{mime};base64,{data}"
    except Exception:
        return None

def clean_exec(exec_str):
    """
    Strip .desktop Exec field codes (%f, %F, %u, %U, %i, %c, %k …) and
    return a list of tokens ready to pass to Popen.
    """
    # Remove field codes
    tokens = shlex.split(exec_str)
    cleaned = [t for t in tokens if not (t.startswith("%") and len(t) == 2)]
    return cleaned

# ── .desktop write-back ──────────────────────────────────────────────────────
def _write_desktop_keys(path, updates):
    """Set/delete keys inside the [Desktop Entry] section of a .desktop file.
    updates: {key: value} to set, {key: None} to delete the line."""
    # surrogateescape round-trips arbitrary bytes even if the file isn't UTF-8
    with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
        lines = f.read().splitlines(keepends=True)
    out, in_section, section_end, seen = [], False, None, set()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section:
                section_end = len(out)
            in_section = stripped == "[Desktop Entry]"
            out.append(line)
            continue
        if in_section and "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                seen.add(key)
                if updates[key] is None:
                    continue
                out.append(f"{key}={updates[key]}\n")
                continue
        out.append(line)
    if in_section:
        section_end = len(out)
    missing = [f"{k}={v}\n" for k, v in updates.items() if v is not None and k not in seen]
    if missing:
        idx = len(out) if section_end is None else section_end
        out[idx:idx] = missing
    with open(path, "w") as f:
        f.writelines(out)

def apply_desktop_changes(desktop_file, updates):
    """Write key changes into a .desktop file.

    Writable files (usually under ~/.local/share/applications) are edited in
    place. System files are copied to ~/.local/share/applications first — the
    user copy overrides the system one per XDG rules, so no root/pkexec needed.

    Returns (path_written, used_override). Raises on failure.
    """
    with SAVE_LOCK:
        path = os.path.realpath(desktop_file)
        if not os.path.exists(path):
            raise FileNotFoundError(desktop_file)
        target, overridden = path, False
        if not os.access(path, os.W_OK):
            os.makedirs(USER_DESKTOP_DIR, exist_ok=True)
            target = os.path.join(USER_DESKTOP_DIR, os.path.basename(path))
            if os.path.realpath(target) != path and not os.path.exists(target):
                shutil.copy2(path, target)
            overridden = True
        _write_desktop_keys(target, updates)
        return target, overridden

def _remove_stale_override(desktop_file, app_id):
    """After a package uninstall, drop any ~/.local/share/applications copy so
    no broken launcher is left behind in the system menu."""
    base = os.path.basename(desktop_file) if desktop_file else f"{app_id}.desktop"
    if not base.endswith(".desktop"):
        base += ".desktop"
    try:
        override = os.path.join(USER_DESKTOP_DIR, base)
        if os.path.isfile(override) or os.path.islink(override):
            os.remove(override)
    except Exception:
        pass

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/apps", methods=["GET"])
def api_apps():
    apps = get_desktop_apps()
    meta = load_meta()
    result = []
    for a in apps:
        app_id = a["id"]
        custom = meta.get(app_id, {})
        icon_b64 = None
        if custom.get("icon_file") and os.path.exists(custom["icon_file"]):
            fname = os.path.basename(custom["icon_file"])
            icon_b64 = f"/api/icon/{fname}"
        elif custom.get("icon_b64"):
            icon_b64 = custom["icon_b64"]
        else:
            icon_b64 = resolve_icon(a.get("Icon", ""))
        result.append({
            "id": app_id,
            "name": custom.get("name") or a.get("Name", app_id),
            "original_name": a.get("Name", app_id),
            "icon": icon_b64,
            "comment": a.get("Comment", ""),
            "categories": a.get("Categories", ""),
            "exec": a.get("Exec", ""),
            "desktop_file": a.get("desktop_file", ""),
            "type": a.get("Type", "Application"),
            "system_hidden": bool(a.get("system_hidden")),
        })
    result.sort(key=lambda x: x["name"].lower())
    return jsonify(result)

@app.route("/api/launch", methods=["POST"])
def api_launch():
    """
    Launch an application by running its Exec command detached from this process.

    Body JSON:
        id          – app id (used for logging / error messages)
        exec        – Exec string from the .desktop file  (preferred)
        desktop_file – path to .desktop file (fallback via gtk-launch)
    """
    data        = request.json or {}
    app_id      = data.get("id", "")
    exec_str    = data.get("exec", "").strip()
    desktop_file = data.get("desktop_file", "").strip()

    if not exec_str and not desktop_file:
        return jsonify({"ok": False, "error": "No exec command or desktop file provided"}), 400

    try:
        env = os.environ.copy()
        # Ensure a DISPLAY is set for X11 apps; Wayland falls back gracefully
        env.setdefault("DISPLAY", ":0")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS",
                       f"unix:path=/run/user/{os.getuid()}/bus")

        if exec_str:
            cmd = clean_exec(exec_str)
            if not cmd:
                raise ValueError(f"Exec string '{exec_str}' produced an empty command after cleaning")
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,   # detach from Flask's process group
            )
        else:
            # gtk-launch accepts a .desktop basename or full path
            desktop_basename = os.path.basename(desktop_file)
            if desktop_basename.endswith(".desktop"):
                desktop_basename = desktop_basename[:-8]
            proc = subprocess.Popen(
                ["gtk-launch", desktop_basename],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

        return jsonify({"ok": True, "pid": proc.pid})

    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": f"Executable not found: {e}"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/rename", methods=["POST"])
def api_rename():
    data = request.json
    app_id = data.get("id")
    new_name = data.get("name", "").strip()
    desktop_file = data.get("desktop_file", "").strip()
    if not app_id or not new_name:
        return jsonify({"ok": False, "error": "Missing id or name"}), 400
    new_name = new_name.replace("\n", " ").replace("\r", " ")
    desktop = {"ok": False}
    with SAVE_LOCK:
        meta = load_meta()
        if app_id not in meta:
            meta[app_id] = {}
        meta[app_id]["name"] = new_name
        save_meta(meta)

        # Also write Name= into the .desktop file so the system app menu updates
        if desktop_file:
            try:
                target, ovr = apply_desktop_changes(desktop_file, {"Name": new_name})
                desktop = {"ok": True, "path": target, "override": ovr}
            except Exception as e:
                desktop = {"ok": False, "error": str(e)}
    return jsonify({"ok": True, "desktop": desktop})

@app.route("/api/set_icon", methods=["POST"])
def api_set_icon():
    data = request.json
    app_id = data.get("id")
    icon_b64 = data.get("icon_b64", "")   # full data URI: "data:image/png;base64,..."
    desktop_file = data.get("desktop_file", "").strip()
    if not app_id or not icon_b64:
        return jsonify({"ok": False, "error": "Missing id or icon data"}), 400

    try:
        header, encoded = icon_b64.split(",", 1)
        ext = "png"
        if "svg" in header:
            ext = "svg"
        elif "jpeg" in header or "jpg" in header:
            ext = "jpg"
        elif "webp" in header:
            ext = "webp"
        raw = base64.b64decode(encoded)
        os.makedirs(ICONS_DIR, exist_ok=True)
        fname = f"{app_id}.{ext}"
        fpath = os.path.join(ICONS_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(raw)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Icon save failed: {e}"}), 500

    desktop = {"ok": False}
    with SAVE_LOCK:
        meta = load_meta()
        if app_id not in meta:
            meta[app_id] = {}
        meta[app_id]["icon_file"] = fpath
        meta[app_id].pop("icon_b64", None)
        save_meta(meta)

        # Point the .desktop Icon= at the saved file so the system menu updates
        if desktop_file:
            try:
                target, ovr = apply_desktop_changes(desktop_file, {"Icon": fpath})
                desktop = {"ok": True, "path": target, "override": ovr}
            except Exception as e:
                desktop = {"ok": False, "error": str(e)}
    return jsonify({"ok": True, "desktop": desktop})

@app.route("/api/set_visibility", methods=["POST"])
def api_set_visibility():
    """Show/hide an app in the system app menu by toggling Hidden/NoDisplay."""
    data = request.json
    app_id = data.get("id")
    desktop_file = data.get("desktop_file", "").strip()
    visible = bool(data.get("visible"))
    if not app_id or not desktop_file:
        return jsonify({"ok": False, "error": "Missing id or desktop_file"}), 400
    # visible → drop both flags; hidden → Hidden=true (NoDisplay is left alone)
    updates = {"Hidden": None, "NoDisplay": None} if visible else {"Hidden": "true"}
    try:
        target, ovr = apply_desktop_changes(desktop_file, updates)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "path": target, "override": ovr})

@app.route("/api/icon/<path:filename>")
def serve_icon(filename):
    return send_from_directory(ICONS_DIR, filename)

def _apt_package_for_desktop(desktop_file, app_id):
    if desktop_file:
        try:
            r = subprocess.run(
                ["dpkg", "-S", desktop_file],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0 and ":" in r.stdout:
                pkg = r.stdout.split(":")[0].strip()
                if pkg:
                    return pkg
        except Exception:
            pass
    return None  # Don't guess!

@app.route("/api/uninstall", methods=["POST"])
def api_uninstall():
    data = request.json
    app_id      = data.get("id")
    desktop_file = data.get("desktop_file", "")
    if not app_id:
        return jsonify({"ok": False, "error": "Missing id"}), 400

    errors = []

    # ── 1. Try apt ────────────────────────────────────────────────────────
    pkg = _apt_package_for_desktop(desktop_file, app_id)
    if pkg is None:
        errors.append("Could not determine package name — skipping apt removal")
    elif pkg in PROTECTED_PACKAGES:
        return jsonify({
            "ok": False,
            "error": f"'{pkg}' is a protected system package and cannot be removed."
        }), 403
    else:
        try:
            env = os.environ.copy()
            env["DEBIAN_FRONTEND"] = "noninteractive"
            result = subprocess.run(
                ["pkexec", "apt-get", "remove", "-y", "--purge", pkg],
                capture_output=True, text=True, timeout=120, env=env
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            if result.returncode == 0:
                with SAVE_LOCK:
                    meta = load_meta()
                    meta.pop(app_id, None)
                    save_meta(meta)
                _remove_stale_override(desktop_file, app_id)
                return jsonify({"ok": True, "method": f"apt ({pkg})"})
            errors.append(f"apt-get remove '{pkg}' → rc={result.returncode} | {stderr or stdout}")
        except subprocess.TimeoutExpired:
            errors.append("apt timed out (120s) — pkexec may be waiting for a polkit agent")
        except FileNotFoundError:
            errors.append("pkexec not found — install policykit-1")
        except Exception as e:
            errors.append(f"apt exception: {e}")

    # ── 2. Try flatpak ────────────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["flatpak", "uninstall", "-y", "--noninteractive", app_id],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            with SAVE_LOCK:
                meta = load_meta()
                meta.pop(app_id, None)
                save_meta(meta)
            _remove_stale_override(desktop_file, app_id)
            return jsonify({"ok": True, "method": "flatpak"})
        errors.append(f"flatpak → rc={result.returncode} | {result.stderr.strip() or result.stdout.strip()}")
    except FileNotFoundError:
        errors.append("flatpak not installed")
    except Exception as e:
        errors.append(f"flatpak exception: {e}")

    # ── 3. Fallback: remove just the .desktop launcher ────────────────────
    if desktop_file and os.path.exists(desktop_file):
        try:
            if desktop_file.startswith(os.path.expanduser("~")):
                os.remove(desktop_file)
            else:
                subprocess.run(["pkexec", "rm", "-f", desktop_file],
                               check=True, timeout=30)
            with SAVE_LOCK:
                meta = load_meta()
                meta.pop(app_id, None)
                save_meta(meta)
            return jsonify({
                "ok": True,
                "method": "desktop_only",
                "warning": "Launcher removed but package is still installed. Use 'sudo apt remove' manually."
            })
        except Exception as e:
            errors.append(f"desktop file removal: {e}")

    return jsonify({"ok": False, "error": "\n".join(errors)}), 500

@app.route("/api/reset_meta", methods=["POST"])
def api_reset_meta():
    data = request.json
    app_id = data.get("id")
    if not app_id:
        return jsonify({"ok": False}), 400
    with SAVE_LOCK:
        meta = load_meta()
        meta.pop(app_id, None)
        save_meta(meta)
    return jsonify({"ok": True})

if __name__ == "__main__":
    from werkzeug.serving import make_server

    # Port 0 asks the OS for a random free loopback port. This keeps every
    # launch on a different port and avoids collisions with other services.
    server = make_server("127.0.0.1", 0, app, threaded=True)
    print(f"App Manager running → http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()