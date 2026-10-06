"""回放解析：按"不晚于截止时间的最近记录"取档，revisit 按摘要复用更早响应，
跟随重定向并检测环。绝不借用晚于截止时间的版本。
"""
import json
from urllib.parse import urljoin

from . import config

REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class Miss(Exception):
    """缺档。reason 为人类可读原因。"""

    def __init__(self, reason, chain=None):
        super().__init__(reason)
        self.reason = reason
        self.chain = chain or []


def find_record(conn, archive_id, url, cutoff_epoch):
    """该 URL 不晚于截止时间的最近一条记录。"""
    return conn.execute(
        """SELECT * FROM records
           WHERE archive_id=? AND url=? AND ts_epoch<=?
           ORDER BY ts_epoch DESC, id DESC LIMIT 1""",
        (archive_id, url, cutoff_epoch),
    ).fetchone()


def has_future_record(conn, archive_id, url, cutoff_epoch):
    return conn.execute(
        "SELECT 1 FROM records WHERE archive_id=? AND url=? AND ts_epoch>? LIMIT 1",
        (archive_id, url, cutoff_epoch),
    ).fetchone() is not None


def find_revisit_base(conn, archive_id, digest, revisit_ts_epoch):
    """revisit 的基体：同一摘要、不晚于该 revisit 采集时间的最近一条带正文记录。
    允许跨 URL；不借未来版本。"""
    return conn.execute(
        """SELECT * FROM records
           WHERE archive_id=? AND digest=? AND ts_epoch<=?
             AND payload IS NOT NULL AND record_type IN ('response','resource')
           ORDER BY ts_epoch DESC, id DESC LIMIT 1""",
        (archive_id, digest, revisit_ts_epoch),
    ).fetchone()


def _headers_to_dict(http_headers_json):
    result = {}
    if http_headers_json:
        for name, value in json.loads(http_headers_json):
            result[name.lower()] = value
    return result


def resolve_body(conn, archive_id, rec):
    """返回 (payload, http_status, headers_dict, base_note)。
    revisit 复用更早响应的正文，但保留自身 URL 与采集时间（由调用方记录）。"""
    headers = _headers_to_dict(rec["http_headers"])
    if rec["record_type"] == "revisit":
        if not rec["digest"]:
            raise Miss(f"revisit 记录缺少负载摘要，无法定位基体（{rec['ts']}）")
        base = find_revisit_base(conn, archive_id, rec["digest"], rec["ts_epoch"])
        if base is None:
            raise Miss(
                f"revisit 缺少基体：找不到摘要 {rec['digest']} 的更早响应，"
                "且不借未来版本"
            )
        payload = base["payload"]
        # HTTP 状态/头优先用 revisit 自身的，缺失时回落到基体
        status = rec["http_status"] if rec["http_status"] is not None else base["http_status"]
        if not headers:
            headers = _headers_to_dict(base["http_headers"])
        base_note = f"revisit 复用 {base['url']} @ {base['ts']} 的正文"
        return payload, status, headers, base_note
    if rec["payload"] is None:
        raise Miss(f"记录无正文（{rec['record_type']} @ {rec['ts']}）")
    return rec["payload"], rec["http_status"], headers, None


def resolve_chain(conn, archive_id, url, cutoff_epoch, on_hop=None):
    """从 url 起解析重定向链，返回最终结果 dict。

    on_hop(url, status, hit_ts, location) 在每个重定向跳点回调（用于记日志）。
    缺档/成环抛 Miss。
    """
    chain = []
    visited = set()
    current = url
    while True:
        if current in visited:
            path = [h["url"] for h in chain] + [current]
            raise Miss(f"检测到重定向环：{' -> '.join(path)}", chain)
        visited.add(current)

        rec = find_record(conn, archive_id, current, cutoff_epoch)
        if rec is None:
            if has_future_record(conn, archive_id, current, cutoff_epoch):
                raise Miss(
                    f"仅有晚于截止时间的记录，不借未来版本：{current}", chain)
            raise Miss(f"截止时间前无此 URL 的任何记录：{current}", chain)

        try:
            payload, status, headers, base_note = resolve_body(conn, archive_id, rec)
        except Miss as exc:
            exc.chain = chain
            raise

        location = headers.get("location")
        if status in REDIRECT_STATUSES and location:
            nxt = urljoin(current, location)
            chain.append({
                "url": current, "status": status, "ts": rec["ts"],
                "location": nxt,
            })
            if on_hop:
                on_hop(current, status, rec["ts"], nxt)
            if len(chain) >= config.MAX_REDIRECTS:
                raise Miss(f"重定向链过长（>{config.MAX_REDIRECTS} 跳）", chain)
            current = nxt
            continue

        return {
            "record": rec,
            "url": current,          # 最终页面 URL（相对引用按它解析）
            "payload": payload,
            "status": status or 200,
            "headers": headers,
            "chain": chain,
            "base_note": base_note,
        }
