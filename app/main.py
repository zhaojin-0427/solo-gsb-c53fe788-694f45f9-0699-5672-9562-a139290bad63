"""启动入口：同一进程内跑两个源——管理界面与回放预览。"""
import os
import threading

import uvicorn

from . import db

ADMIN_PORT = int(os.environ.get("ADMIN_PORT", "8000"))
PREVIEW_PORT = int(os.environ.get("PREVIEW_PORT", "8001"))


def main():
    db.init()
    from .admin import app as admin_app
    from .preview import app as preview_app

    admin_server = uvicorn.Server(
        uvicorn.Config(admin_app, host="0.0.0.0", port=ADMIN_PORT, log_level="info")
    )
    preview_server = uvicorn.Server(
        uvicorn.Config(preview_app, host="0.0.0.0", port=PREVIEW_PORT, log_level="info")
    )
    threading.Thread(target=admin_server.run, daemon=True).start()
    print("管理界面: http://localhost:%d  回放预览: http://localhost:%d" % (ADMIN_PORT, PREVIEW_PORT))
    preview_server.run()


if __name__ == "__main__":
    main()
