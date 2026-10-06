"""管理源应用：上传档案、查看捕获、缺档清单。与预览源分端口隔离。"""
import os
import shutil
import threading

from fastapi import FastAPI, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, importer, replaycore

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PREVIEW_PORT = int(os.environ.get("PREVIEW_PORT", "8001"))

app = FastAPI(title="WARC Replay Admin", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=FileResponse)
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "admin.html"))


@app.get("/api/config")
async def config():
    return {"preview_port": PREVIEW_PORT}


@app.post("/api/archives")
async def upload_archive(file: UploadFile):
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO archives (filename, status) VALUES (?, 'importing')",
            (file.filename or "unnamed.warc",),
        )
        archive_id = cur.lastrowid
        safe_name = os.path.basename(file.filename or "unnamed.warc").replace("/", "_")
        stored = os.path.join(db.UPLOAD_DIR, "%d_%s" % (archive_id, safe_name))
        with open(stored, "wb") as out:
            shutil.copyfileobj(file.file, out)
        conn.execute("UPDATE archives SET stored_path=? WHERE id=?", (stored, archive_id))
        conn.commit()
    finally:
        conn.close()
    threading.Thread(
        target=importer.import_archive, args=(archive_id, stored), daemon=True
    ).start()
    return {"id": archive_id, "status": "importing"}


@app.get("/api/archives")
async def list_archives():
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, filename, status, error, record_count, imported_at "
            "FROM archives ORDER BY id DESC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@app.delete("/api/archives/{archive_id}")
async def delete_archive(archive_id: int):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT stored_path FROM archives WHERE id=?", (archive_id,)
        ).fetchone()
        if row is None:
            return JSONResponse({"error": "档案不存在"}, status_code=404)
        conn.execute("DELETE FROM archives WHERE id=?", (archive_id,))
        conn.commit()
    finally:
        conn.close()
    if row["stored_path"] and os.path.exists(row["stored_path"]):
        os.remove(row["stored_path"])
    return {"deleted": archive_id}


@app.get("/api/captures")
async def captures(url: str):
    conn = db.connect()
    try:
        rows = []
        for cand in replaycore.url_variants(url):
            rows.extend(
                conn.execute(
                    "SELECT url, ts, type, status, digest, length(body) AS size "
                    "FROM records WHERE url=? ORDER BY epoch DESC LIMIT 200",
                    (cand,),
                ).fetchall()
            )
    finally:
        conn.close()
    out = [dict(r) for r in rows]
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out[:200]


@app.get("/api/missing")
async def list_missing():
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, url, cutoff, reason, referer, epoch FROM missing "
            "ORDER BY epoch DESC LIMIT 500"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@app.delete("/api/missing")
async def clear_missing():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM missing")
        conn.commit()
    finally:
        conn.close()
    return {"cleared": True}
