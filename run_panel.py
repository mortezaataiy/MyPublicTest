# -*- coding: utf-8 -*-
"""نقطه ورود سرویس: uvicorn run_panel:app"""

import os

from panel.main import app  # noqa: F401  (uvicorn این را صدا می‌زند)

if __name__ == "__main__":  # اجرای مستقیم برای تست محلی
    import uvicorn

    uvicorn.run(
        "run_panel:app",
        host=os.environ.get("PANEL_HOST", "127.0.0.1"),
        port=int(os.environ.get("PANEL_PORT", "8000")),
    )
