"""回放解析：按截止时间取记录、revisit 复用基体、跟随重定向并检测环。"""
import json
from urllib.parse import urljoin, urlsplit, urlunsplit

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_HOPS = 20


class ReplayError(Exception):
    """回放失败：url 为出问题的地址，reason 为缺档/错误原因。"""

    def __init__(self, url, reason, chain=None):
        super().__init__(reason)
        self.url = url
        self.reason = reason
        self.chain = chain or []


def url_variants(url):
    """精确匹配之外，兼容 'http://host' 与 'http://host/' 的差异。"""
    variants = [url]
    parts = urlsplit(url)
    if parts.path in ("", "/"):
        alt_path = "" if parts.path == "/" else "/"
        variants.append(urlunsplit((parts.scheme, parts.netloc, alt_path, parts.query, "")))
    return variants


def find_record(conn, url, cutoff):
    """该 URL 不晚于截止时间的最近一条记录（不借未来版本）。"""
    for cand in url_variants(url):
        row = conn.execute(
            "SELECT * FROM records WHERE url=? AND epoch<=? "
            "ORDER BY epoch DESC, id DESC LIMIT 1",
            (cand, cutoff),
        ).fetchone()
        if row is not None:
            return row
    return None


def find_base(conn, digest, before_epoch):
    """按摘要找不晚于 revisit 采集时间的、带正文的基体记录（允许跨 URL）。"""
    return conn.execute(
        "SELECT * FROM records WHERE digest=? AND body IS NOT NULL "
        "AND type!='revisit' AND epoch<=? ORDER BY epoch DESC, id DESC LIMIT 1",
        (digest, before_epoch),
    ).fetchone()


def header_value(headers, name):
    name = name.lower()
    for k, v in headers:
        if k.lower() == name:
            return v
    return None


def materialize(conn, row):
    """把一条记录实体化为可服务的命中；revisit 在此复用基体正文。

    保留 revisit 自身的 URL 与采集时间，只从基体借正文（以及缺失时的
    头部/类型/状态码）。基体必须不晚于 revisit 采集时间，绝不借未来版本。
    """
    headers = json.loads(row["headers"]) if row["headers"] else []
    hit = {
        "url": row["url"],
        "ts": row["ts"],
        "epoch": row["epoch"],
        "type": row["type"],
        "status": row["status"],
        "mime": row["mime"],
        "headers": headers,
        "body": row["body"],
        "base_url": None,
        "base_ts": None,
    }
    if row["type"] == "revisit":
        if not row["digest"]:
            raise ReplayError(row["url"], "revisit 记录缺少摘要，无法定位基体")
        base = find_base(conn, row["digest"], row["epoch"])
        if base is None:
            raise ReplayError(
                row["url"],
                "revisit 缺少基体：按摘要未找到不晚于采集时间的响应记录",
            )
        hit["body"] = base["body"]
        if not hit["headers"]:
            hit["headers"] = json.loads(base["headers"]) if base["headers"] else []
        if not hit["mime"]:
            hit["mime"] = base["mime"]
        if hit["status"] is None:
            hit["status"] = base["status"]
        hit["base_url"] = base["url"]
        hit["base_ts"] = base["ts"]
    return hit


def follow(conn, url, cutoff):
    """从 url 起解析并跟随重定向，返回命中链（最后一跳为最终内容）。

    每一跳都独立按"该 URL 不晚于截止时间的最近记录"解析；
    发现环或超过跳数上限即抛 ReplayError。
    """
    chain = []
    seen = set()
    current = url
    while True:
        if current in seen:
            raise ReplayError(current, "检测到重定向环", chain)
        seen.add(current)
        if len(chain) >= MAX_HOPS:
            raise ReplayError(current, "重定向次数超过上限", chain)
        row = find_record(conn, current, cutoff)
        if row is None:
            raise ReplayError(current, "无早于或等于截止时间的记录", chain)
        try:
            hit = materialize(conn, row)
        except ReplayError as exc:
            exc.chain = chain
            raise
        chain.append(hit)
        if hit["status"] in REDIRECT_STATUSES:
            location = header_value(hit["headers"], "location")
            if location:
                current = urljoin(current, location)
                continue
        return chain
