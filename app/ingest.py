"""WARC/WARC.gz 导入：解析记录、抽取 HTTP 响应与 revisit 元数据、计算摘要。

损坏档案（gzip 破损、记录畸形、无任何可用记录）整体导入失败并回滚。
"""
import base64
import hashlib
import json
import os
from datetime import datetime, timezone

from warcio.archiveiterator import ArchiveIterator
from warcio.exceptions import ArchiveLoadFailed

from . import config, db


class IngestError(Exception):
    """档案损坏或无可用记录。"""


def _clean(message, limit=200):
    """错误信息去控制字符并截断，避免把损坏数据原样回显。"""
    text = "".join(c if c.isprintable() else "?" for c in str(message))
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_warc_date(value):
    """WARC-Date -> (iso 字符串, epoch 秒)。解析失败返回 None。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat(), dt.timestamp()
    except ValueError:
        return None


def norm_digest(digest):
    """摘要归一化：algo 小写、哈希小写，便于跨记录比较。"""
    if not digest:
        return None
    digest = digest.strip()
    if ":" in digest:
        algo, h = digest.split(":", 1)
        return algo.strip().lower() + ":" + h.strip().lower()
    return digest.lower()


def sha1_digest(payload):
    return "sha1:" + base64.b32encode(hashlib.sha1(payload).digest()).decode("ascii").lower()


def _extract_record(record):
    """从 warcio 记录中抽取入库字段；无 URL/时间的记录返回 None。"""
    rtype = record.rec_type
    if rtype not in ("response", "revisit", "resource"):
        return None
    url = record.rec_headers.get_header("WARC-Target-URI")
    parsed = parse_warc_date(record.rec_headers.get_header("WARC-Date"))
    if not url or not parsed:
        return None
    ts_iso, ts_epoch = parsed

    http_status = None
    http_headers = None
    content_type = None
    payload = None

    if record.http_headers is not None:
        # response / revisit：带 HTTP 头
        try:
            http_status = int(record.http_headers.get_statuscode())
        except (TypeError, ValueError):
            http_status = None
        http_headers = json.dumps(record.http_headers.headers, ensure_ascii=False)
        content_type = record.http_headers.get_header("Content-Type")
        payload = record.content_stream().read()
    elif rtype == "resource":
        content_type = record.rec_headers.get_header("Content-Type")
        payload = record.content_stream().read()

    if rtype == "revisit":
        payload = None  # revisit 无正文，正文按摘要复用更早响应

    digest = norm_digest(record.rec_headers.get_header("WARC-Payload-Digest"))
    if payload and not digest:
        digest = sha1_digest(payload)

    return {
        "url": url,
        "ts": ts_iso,
        "ts_epoch": ts_epoch,
        "record_type": rtype,
        "http_status": http_status,
        "http_headers": http_headers,
        "content_type": content_type,
        "digest": digest,
        "refers_to": record.rec_headers.get_header("WARC-Refers-To"),
        "payload": payload,
    }


def ingest_file(path, filename):
    """导入 WARC 文件，返回 archive_id；失败抛 IngestError（不留半成品）。"""
    db.init_db()
    conn = db.connect()
    archive_id = None
    count = 0
    try:
        cur = conn.execute(
            "INSERT INTO archives(filename, uploaded_at, status) VALUES (?,?, 'importing')",
            (filename, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        archive_id = cur.lastrowid

        with open(path, "rb") as fh:
            iterator = ArchiveIterator(fh)
            while True:
                try:
                    record = next(iterator, None)
                except (ArchiveLoadFailed, EOFError, ValueError, OSError) as exc:
                    raise IngestError(f"档案损坏：{_clean(exc)}") from exc
                if record is None:
                    break
                try:
                    row = _extract_record(record)
                except (ArchiveLoadFailed, EOFError, ValueError, OSError) as exc:
                    raise IngestError(f"档案损坏（记录解析失败）：{_clean(exc)}") from exc
                if row is None:
                    continue
                conn.execute(
                    """INSERT INTO records
                       (archive_id, url, ts, ts_epoch, record_type, http_status,
                        http_headers, content_type, digest, refers_to, payload)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (archive_id, row["url"], row["ts"], row["ts_epoch"],
                     row["record_type"], row["http_status"], row["http_headers"],
                     row["content_type"], row["digest"], row["refers_to"],
                     row["payload"]),
                )
                count += 1

        if count == 0:
            raise IngestError("档案中没有任何可用的 response/revisit/resource 记录")

        conn.execute(
            "UPDATE archives SET status='ok', record_count=? WHERE id=?",
            (count, archive_id),
        )
        conn.commit()
        return archive_id, count
    except IngestError as exc:
        _mark_failed(conn, archive_id, str(exc))
        raise
    except Exception as exc:  # 任何意外错误同样视为导入失败
        _mark_failed(conn, archive_id, f"档案损坏：{_clean(exc)}")
        raise IngestError(f"档案损坏：{_clean(exc)}") from exc
    finally:
        conn.close()


def _mark_failed(conn, archive_id, error):
    if archive_id is None:
        return
    conn.execute("DELETE FROM records WHERE archive_id=?", (archive_id,))
    conn.execute("UPDATE archives SET status='failed', error=? WHERE id=?",
                 (error, archive_id))
    conn.commit()


def save_upload(upload_file):
    """把上传流写入临时文件，返回路径。"""
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    tmp = os.path.join(config.UPLOAD_DIR,
                       f"up-{datetime.now(timezone.utc).timestamp()}.warc")
    with open(tmp, "wb") as out:
        while True:
            chunk = upload_file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return tmp
