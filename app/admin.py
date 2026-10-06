"""管理端：上传档案、选择入口 URL 与截止时间、查看回放记录与缺档清单。

管理端与回放源运行在不同端口（不同源），相互隔离。
"""
import os

from fastapi import FastAPI, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from . import config, db, ingest

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def create_admin_app():
    db.init_db()
    app = FastAPI(title="WARC 回放台 · 管理端")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "admin.html"))

    @app.get("/static/{name}")
    def static_files(name: str):
        path = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(path):
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(path)

    @app.get("/api/config")
    def get_config():
        return {"replay_port": config.REPLAY_PORT, "admin_port": config.ADMIN_PORT}

    # ---- 档案管理 ----
    @app.post("/api/archives")
    async def upload(file: UploadFile):
        tmp = ingest.save_upload(file.file)
        try:
            archive_id, count = ingest.ingest_file(tmp, file.filename or "upload.warc")
        except ingest.IngestError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return {"id": archive_id, "records": count}

    @app.get("/api/archives")
    def list_archives():
        conn = db.connect()
        try:
            rows = conn.execute(
                "SELECT id, filename, uploaded_at, status, error, record_count "
                "FROM archives ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @app.delete("/api/archives/{archive_id}")
    def delete_archive(archive_id: int):
        conn = db.connect()
        try:
            conn.execute("DELETE FROM records WHERE archive_id=?", (archive_id,))
            conn.execute("DELETE FROM access_log WHERE archive_id=?", (archive_id,))
            cur = conn.execute("DELETE FROM archives WHERE id=?", (archive_id,))
            conn.commit()
            return {"deleted": cur.rowcount}
        finally:
            conn.close()

    @app.get("/api/archives/{archive_id}/urls")
    def list_urls(archive_id: int):
        conn = db.connect()
        try:
            rows = conn.execute(
                """SELECT url, COUNT(*) AS captures,
                          MIN(ts) AS first_ts, MAX(ts) AS last_ts
                   FROM records WHERE archive_id=?
                   GROUP BY url ORDER BY url""",
                (archive_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ---- 回放记录 / 缺档清单 ----
    @app.get("/api/archives/{archive_id}/log")
    def access_log(archive_id: int, missing_only: bool = False, limit: int = 500):
        conn = db.connect()
        try:
            sql = ("SELECT id, logged_at, requested_url, cutoff_epoch, hit,"
                   " http_status, hit_ts, record_type, reason, via"
                   " FROM access_log WHERE archive_id=?")
            if missing_only:
                sql += " AND hit=0"
            sql += " ORDER BY id DESC LIMIT ?"
            rows = conn.execute(sql, (archive_id, limit)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @app.post("/api/archives/{archive_id}/log/clear")
    def clear_log(archive_id: int):
        conn = db.connect()
        try:
            conn.execute("DELETE FROM access_log WHERE archive_id=?", (archive_id,))
            conn.commit()
            return {"cleared": True}
        finally:
            conn.close()

    return app
