"""Windows tray integration for the packaged desktop application."""

import threading

import pystray
from PIL import Image


def run_with_tray(server, icon_path):
    """Run the HTTP server beside a tray icon with an explicit exit action."""
    icon_image = Image.open(icon_path).convert("RGBA")

    def exit_application(icon, _item):
        icon.stop()
        server.shutdown()

    icon = pystray.Icon(
        "FSAtlas",
        icon_image,
        "FSAtlas",
        pystray.Menu(pystray.MenuItem("Exit", exit_application)),
    )
    server_thread = threading.Thread(target=server.serve_forever, name="FSAtlas server")
    server_thread.start()
    try:
        icon.run()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()
        icon_image.close()