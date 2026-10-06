"""入口：同一进程内启动两个源 —— 管理端（ADMIN_PORT）与回放源（REPLAY_PORT）。"""
import threading

import uvicorn

from . import config
from .admin import create_admin_app
from .replay import create_replay_app


def main():
    replay_app = create_replay_app()
    admin_app = create_admin_app()

    replay_server = uvicorn.Server(uvicorn.Config(
        replay_app, host="0.0.0.0", port=config.REPLAY_PORT,
        log_level="info", access_log=False))
    threading.Thread(target=replay_server.run, daemon=True).start()

    print(f"管理端:  http://localhost:{config.ADMIN_PORT}")
    print(f"回放源:  http://localhost:{config.REPLAY_PORT}")
    uvicorn.run(admin_app, host="0.0.0.0", port=config.ADMIN_PORT,
                log_level="info", access_log=False)


if __name__ == "__main__":
    main()
