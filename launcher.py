#!/usr/bin/env python3
"""App Manager – native desktop launcher.

Starts the Flask backend on a random free loopback port and shows the UI in an
embedded GTK/WebKit window, so the app behaves like a normal desktop app
instead of a script that opens a browser tab.
"""

import os
import sys
import threading

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

WINDOW_TITLE = "App Manager"


def start_server():
    """Start Flask on an OS-assigned free loopback port.

    ``make_server`` binds immediately, so ``server_port`` is the real port the
    kernel chose (port 0 = ephemeral). That removes any race between picking a
    port and binding it.
    """
    from werkzeug.serving import make_server
    from app import app as flask_app

    server = make_server("127.0.0.1", 0, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_port


def load_webkit():
    """Return the WebKit2 namespace, trying the 4.1 then 4.0 typelibs."""
    import gi

    gi.require_version("Gtk", "3.0")
    for version in ("4.1", "4.0"):
        try:
            gi.require_version("WebKit2", version)
            from gi.repository import WebKit2
            return WebKit2
        except (ValueError, ImportError):
            continue
    raise ImportError(
        "WebKit2GTK typelib not found. Install gir1.2-webkit2-4.1."
    )


def run_window(url, server):
    import gi

    WebKit2 = load_webkit()
    from gi.repository import Gdk, GLib, Gtk

    Gdk.set_program_class("AppManager")

    window = Gtk.Window(title=WINDOW_TITLE)
    window.set_default_size(1100, 700)
    window.set_size_request(800, 500)
    window.set_icon_name("app-manager")

    # Match the UI background so there is no white flash while WebKit loads.
    css = Gtk.CssProvider()
    css.load_from_data(b"window { background-color: #0e0f11; }")
    window.get_style_context().add_provider(
        css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    webview = WebKit2.WebView()
    webview.load_uri(url)
    window.add(webview)

    def on_destroy(*_args):
        Gtk.main_quit()

    window.connect("destroy", on_destroy)
    window.show_all()
    Gtk.main()
    server.shutdown()


def show_error(message):
    """Show a small GTK error dialog instead of dying silently."""
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text="App Manager could not start",
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()
    except Exception:
        print(message, file=sys.stderr)


def main():
    os.chdir(APP_DIR)

    try:
        server, port = start_server()
    except Exception as exc:
        show_error(f"Failed to start the backend: {exc}")
        return 1

    url = f"http://127.0.0.1:{port}"
    print(f"App Manager running → {url}")

    try:
        run_window(url, server)
    except ImportError as exc:
        server.shutdown()
        show_error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
