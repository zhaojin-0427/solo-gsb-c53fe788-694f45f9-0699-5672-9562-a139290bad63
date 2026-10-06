"""全局配置：端口、数据目录、回放隔离策略。"""
import os

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
DB_PATH = os.path.join(DATA_DIR, "replay.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

ADMIN_PORT = int(os.environ.get("ADMIN_PORT", "8000"))
REPLAY_PORT = int(os.environ.get("REPLAY_PORT", "8001"))

# 允许把回放页嵌入 iframe 的管理端来源（逗号分隔）
ADMIN_ORIGINS = os.environ.get(
    "ADMIN_ORIGINS",
    "http://localhost:8000 http://127.0.0.1:8000",
).replace(",", " ")

# 回放源的 CSP：禁止页面访问实时外网，只允许同源（归档内容）与 data:/blob:
REPLAY_CSP = (
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "media-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-src 'self'; "
    "object-src 'self'; "
    "child-src 'self'; "
    "worker-src 'self' blob:; "
    "base-uri 'self'; "
    "form-action 'self'; "
    f"frame-ancestors 'self' {ADMIN_ORIGINS}"
)

MAX_REDIRECTS = 20
