import io

try:
    import qrcode
except ImportError:  # pragma: no cover
    qrcode = None

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402


def make_qr_texture(url: str) -> object | None:
    """Erzeugt ein Gdk.Texture aus einer URL als QR-Code. Gibt None zurück wenn qrcode nicht installiert ist."""
    if qrcode is None:
        return None
    img = qrcode.make(url)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(buffer.getvalue())
    loader.close()
    pixbuf = loader.get_pixbuf()
    return Gdk.Texture.new_for_pixbuf(pixbuf)
