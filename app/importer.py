"""WARC/WARC.gz 导入：解析记录、规范化摘要、损坏即整体失败。"""
import base64
import gzip
import hashlib
import json
import os
import shutil
import tempfile
import zlib
from datetime import datetime, timezone

from warcio.archiveiterator import ArchiveIterator

from . import db


class ArchiveImportError(Exception):
    pass


def open_warc_stream(path):
    """打开并校验 WARC/WARC.gz，返回定位在开头的可读流。

    warcio 会把截断 gzip 的 EOFError 当正常结束吞掉，因此这里先把
    gzip 完整解压一遍（截断/CRC 错误直接抛出），再检查解压后的
    内容以记录分隔空行结尾（WARC 记录以两个换行结尾），防止普通
    WARC 中途截断被静默接受。
    """
    raw = open(path, "rb")
    magic = raw.read(2)
    raw.seek(0)
    if magic == b"\x1f\x8b":
        buf = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
        try:
            with gzip.GzipFile(fileobj=raw) as gz:
                shutil.copyfileobj(gz, buf)  # 损坏在此抛出 EOFError/BadGzipFile
        finally:
            raw.close()
        stream = buf
    else:
        stream = raw

    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.close()
        raise ArchiveImportError("空文件")
    tail = b""
    stream.seek(max(0, stream.tell() - 4), os.SEEK_SET)
    tail = stream.read()
    stream.seek(0)
    if not (tail.endswith(b"\r\n\r\n") or tail.endswith(b"\n\n")):
        stream.close()
        raise ArchiveImportError("档案末尾不完整，可能已截断或损坏")
    return stream


def normalize_digest(value):
    """统一为 'algo:VALUE'（算法小写、值大写），便于字符串相等比较。"""
    if not value:
        return None
    value = value.strip()
    if ":" in value:
        algo, val = value.split(":", 1)
        return algo.strip().lower() + ":" + val.strip().upper()
    return "sha1:" + value.upper()


def compute_digest(body):
    return "sha1:" + base64.b32encode(hashlib.sha1(body).digest()).decode("ascii")


def parse_warc_date(value):
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def header_lookup(headers, name):
    name = name.lower()
    for k, v in headers:
        if k.lower() == name:
            return v
    return None


def dechunk(data):
    """解开 HTTP chunked 传输编码。"""
    out = bytearray()
    pos = 0
    while True:
        nl = data.find(b"\r\n", pos)
        if nl < 0:
            raise ArchiveImportError("chunked 传输编码损坏")
        size = int(data[pos:nl].split(b";")[0].strip(), 16)
        pos = nl + 2
        if size == 0:
            return bytes(out)
        out += data[pos:pos + size]
        pos += size + 2


def is_textual(mime):
    mime = (mime or "").lower()
    return (
        mime.startswith("text/")
        or "javascript" in mime
        or "json" in mime
        or "xml" in mime
        or "svg" in mime
    )


def try_decode_content(encoding, body):
    """解 gzip/deflate，失败返回 None（保留原样）。"""
    try:
        if encoding == "gzip":
            return gzip.decompress(body)
        if encoding == "deflate":
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -15)
    except Exception:
        return None
    return None


def extract_record(record):
    """把一条 WARC 记录转成 records 表行；无关类型返回 None。"""
    rtype = record.rec_type
    if rtype not in ("response", "revisit", "resource"):
        return None
    url = record.rec_headers.get_header("WARC-Target-URI")
    date = record.rec_headers.get_header("WARC-Date")
    if not url or not date:
        return None
    epoch = parse_warc_date(date).timestamp()
    digest = normalize_digest(
        record.rec_headers.get_header("WARC-Payload-Digest")
        or record.rec_headers.get_header("WARC-Block-Digest")
    )
    refers_to = record.rec_headers.get_header("WARC-Refers-To-Target-URI")

    status = None
    mime = None
    headers = []
    body = None

    if record.http_headers is not None:  # application/http 记录
        try:
            status = int(record.http_headers.get_statuscode())
        except (TypeError, ValueError):
            status = None
        headers = [(k, v) for k, v in record.http_headers.headers]
        mime = header_lookup(headers, "Content-Type")
        if rtype != "revisit":
            body = record.content_stream().read()
    else:  # resource 等非 HTTP 记录
        mime = record.rec_headers.get_header("Content-Type")
        if rtype != "revisit":
            body = record.content_stream().read()

    if body is not None:
        te = header_lookup(headers, "Transfer-Encoding")
        if te and "chunked" in te.lower():
            body = dechunk(body)
            headers = [(k, v) for k, v in headers if k.lower() != "transfer-encoding"]
        ce = header_lookup(headers, "Content-Encoding")
        if ce and ce.strip().lower() in ("gzip", "deflate") and is_textual(mime):
            decoded = try_decode_content(ce.strip().lower(), body)
            if decoded is not None:
                body = decoded
                headers = [
                    (k, v)
                    for k, v in headers
                    if k.lower() not in ("content-encoding", "content-length")
                ]

    if body is not None and not digest:
        digest = compute_digest(body)

    return (
        url,
        date,
        epoch,
        rtype,
        status,
        mime,
        digest,
        json.dumps(headers, ensure_ascii=False),
        body,
        refers_to,
    )


def import_archive(archive_id, path):
    """导入整个档案；任何解析错误都使导入整体失败（不留半截数据）。"""
    conn = db.connect()
    try:
        rows = []
        with open_warc_stream(path) as stream:
            for record in ArchiveIterator(stream):
                row = extract_record(record)
                if row is not None:
                    rows.append(row)
        if not rows:
            raise ArchiveImportError("档案中没有任何 response/revisit/resource 记录")

        conn.executemany(
            "INSERT INTO records "
            "(archive_id, url, ts, epoch, type, status, mime, digest, headers, body, refers_to) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(archive_id,) + r for r in rows],
        )
        conn.execute(
            "UPDATE archives SET status='ready', record_count=?, imported_at=? WHERE id=?",
            (len(rows), datetime.now(timezone.utc).isoformat(), archive_id),
        )
        conn.commit()
    except Exception as exc:  # 损坏档案：清理半成品，标记失败
        conn.rollback()
        conn.execute("DELETE FROM records WHERE archive_id=?", (archive_id,))
        conn.execute(
            "UPDATE archives SET status='failed', error=? WHERE id=?",
            (f"{type(exc).__name__}: {exc}", archive_id),
        )
        conn.commit()
    finally:
        conn.close()
