from http import HTTPStatus
from pathlib import Path

MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
SERVER_PORT = 8080
CONTENT_TOO_LARGE = getattr(HTTPStatus, "CONTENT_TOO_LARGE", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
WS_TIMEOUT_SECONDS = 30
CONFIG_DIR = Path.home() / ".config" / "gnome-classshare"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
APP_DESKTOP_ID = "gnome-classshare.desktop"
CLASSSHARE_ROOT = Path.home() / "ClassShare"
CUSTOM_ICON_DIR = Path.home() / ".local" / "share" / "gnome-classshare"
CUSTOM_ICON_PATH = CUSTOM_ICON_DIR / "icon.png"
