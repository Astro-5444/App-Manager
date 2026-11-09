# App Manager

App Manager is a native Linux desktop app that lets you manage your installed applications from one place. You can launch, rename, change icons, hide, or uninstall apps without having to use the terminal.

It runs inside a **GTK/WebKit** window, so it feels like a regular desktop application rather than a website opened in your browser. Behind the scenes, it uses a small Python/Flask backend that picks an available local port each time the app starts.

## Screenshots

<p align="center">
  <img src="Images/App%20Manager.png" alt="App Manager main window" width="760"/>
</p>
<p align="center"><em>Every installed app in one place — run, edit, or remove it with a click.</em></p>

<p align="center">
  <img src="Images/App%20Manager%20-%20Edit%20App.png" alt="Editing an app in App Manager" width="420"/>
</p>
<p align="center"><em>Give an app a new name or icon and the change follows it into your system menu.</em></p>


## Features

- **Browse installed apps** – Automatically finds applications from system, Flatpak, and user `.desktop` directories.
- **Launch apps** – Opens applications independently, without tying them to App Manager.
- **Rename apps** – Changes an app's name in App Manager and the system application menu.
- **Change icons** – Use your own PNG, SVG, JPEG, or WebP image as an app icon.
- **Show / hide apps** – Control whether an application appears in your desktop's application menu.
- **Uninstall apps** – Attempts to remove applications through `apt` (using `pkexec`), Flatpak, or launcher removal. A protected-package list helps prevent accidental removal of important system components.
- **Native desktop window** – Uses GTK and WebKit to display the interface, with a dark theme and no external browser needed.
- **Automatic port selection** – Finds an available local port on every launch to avoid conflicts with other applications.

## Requirements

- Linux with GTK 3 and WebKit2GTK 4.1
- Python 3.8 or newer
- Flask

On Debian, Ubuntu, and Zorin OS, the packaged installer handles these dependencies automatically.

If you want to install them manually:

```bash
sudo apt install python3 python3-flask python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1
```

## Installation (recommended)

Download or build the `.deb` package, then install it:

```bash
sudo apt install ./build/app-manager_1.0.0_amd64.deb
```

Once installed, you can find **App Manager** in your application menu or start it from the terminal:

```bash
app-manager
```

To remove App Manager:

```bash
sudo apt remove app-manager
```

**Note:** Removing App Manager won't affect any of the applications you previously managed with it.

## Build the installer

To create your own Debian package, run:

```bash
./build_deb.sh
```

The generated package will be saved in:

```bash
build/app-manager_<version>_<arch>.deb
```

You can also set a custom version and maintainer name:

```bash
VERSION=1.1.0 MAINTAINER="You <you@example.com>" ./build_deb.sh
```

The package installs the following files:

| Path | Purpose |
|---|---|
| `/usr/bin/app-manager` | Application launcher |
| `/usr/lib/app-manager/` | App source code and frontend files |
| `/usr/share/applications/app-manager.desktop` | Application menu entry |
| `/usr/share/icons/hicolor/.../app-manager.svg` | Application icon |

## Running from source

If you'd rather run the project without installing the `.deb` package:

```bash
./run.sh
```

The script uses the project's `.venv` environment for Flask while keeping the system Python available for GTK and WebKit bindings.

You can also set up the dependencies manually:

```bash
python3 -m venv .venv
.venv/bin/pip install flask
PYTHONPATH="$PWD/.venv/lib/python3.12/site-packages" python3 launcher.py
```

## How it works

App Manager uses a small Flask backend to handle application management, while GTK and WebKit provide the desktop interface.

Here's what happens when you start it:

1. `launcher.py` starts the Flask server using `werkzeug.serving.make_server("127.0.0.1", 0, app)`.
   Using port `0` lets the operating system pick an available port automatically.
2. A GTK window opens with an embedded `WebKit2.WebView`, which loads the frontend from the local Flask server.
3. When you close the window, the Flask server shuts down as well.

The frontend communicates with `app.py` through a small JSON API:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/apps` | `GET` | Get the list of installed applications |
| `/api/launch` | `POST` | Launch an application |
| `/api/rename` | `POST` | Rename an app and update its `.desktop` file |
| `/api/set_icon` | `POST` | Save and apply a custom icon |
| `/api/set_visibility` | `POST` | Show or hide an app in the system menu |
| `/api/uninstall` | `POST` | Uninstall a package or remove its launcher |
| `/api/reset_meta` | `POST` | Reset an app's custom metadata |
| `/api/icon/<file>` | `GET` | Load a saved custom icon |

## Project structure

```bash
app_manager/
├── app.py                  # Flask backend and desktop-file handling
├── launcher.py             # GTK/WebKit window and local server
├── static/index.html       # Frontend interface
├── assets/
│   ├── app-manager.desktop # Desktop menu entry
│   └── app-manager.svg     # Application icon
├── build_deb.sh            # Debian package builder
├── run.sh                  # Run directly from source
└── build/                  # Generated .deb packages
```

## Data and safety

App Manager keeps its custom data in your home directory:

- App metadata: `~/.config/app_manager_meta.json`
- Custom icons: `~/.config/app_manager_icons/`

A few things worth knowing:

- System `.desktop` files aren't overwritten when they aren't writable. Instead, App Manager creates a user-level override in `~/.local/share/applications/`.
- Removing packages through `apt` uses `pkexec`, so your desktop may ask for your password before continuing.
- A built-in list of protected packages helps prevent the removal of essential desktop components, including `gnome-shell`, `gdm3`, and desktop meta-packages.

## Notes

- The interface loads the Inter font from Google Fonts. If you're offline, it falls back to your system's default UI font.
- Removing applications through `apt` requires `policykit-1` and `pkexec`.
- Flatpak removal is available when Flatpak is installed on your system.