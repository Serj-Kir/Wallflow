"""Wallflow — a free cross-platform live wallpaper engine."""

from .version import (
    APP_AUTHOR,
    APP_DESCRIPTION,
    APP_ID,
    APP_LICENSE,
    APP_NAME,
    APP_VERSION,
)

__version__ = APP_VERSION
__all__ = [
    "APP_NAME",
    "APP_ID",
    "APP_VERSION",
    "APP_DESCRIPTION",
    "APP_AUTHOR",
    "APP_LICENSE",
    "__version__",
]