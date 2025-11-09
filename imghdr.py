# imghdr shim for Python 3.13+
# Minimal replacement used by python-telegram-bot 13.x
from typing import Optional

def what(file, h: Optional[bytes] = None):
    try:
        from PIL import Image
        if hasattr(file, "read"):
            pos = file.tell()
            try:
                img = Image.open(file)
            finally:
                try:
                    file.seek(pos)
                except Exception:
                    pass
        else:
            img = Image.open(file)
        fmt = (img.format or "").lower()
        # Return names compatible with legacy imghdr.what
        mapping = {"jpeg": "jpeg", "png": "png", "gif": "gif", "tiff": "tiff", "bmp": "bmp", "webp": "webp"}
        return mapping.get(fmt, None)
    except Exception:
        return None
