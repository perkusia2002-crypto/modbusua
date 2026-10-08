from __future__ import annotations

import uvicorn

from .main import app, settings


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=settings.ui_host,
        port=settings.ui_port,
        log_level="info",
    )
