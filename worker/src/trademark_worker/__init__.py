"""Local CNIPA trademark query worker."""

import os


# The frozen desktop worker does not load third-party Pydantic plugins.  Keeping
# discovery enabled makes importlib scan every bundled OCR distribution before
# the command router starts, and some Windows wheels expose non-text metadata
# through PyInstaller's resource reader.
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")


__version__ = "0.1.29"
