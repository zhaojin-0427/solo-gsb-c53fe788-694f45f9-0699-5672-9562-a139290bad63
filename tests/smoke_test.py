"""冒烟测试：导入样例 WARC，逐条验证回放规则。运行：python tests/smoke_test.py"""
import os
import sys
import tempfile
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="warc-replay-test-")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.admin import create_admin_app  # noqa: E402
from app.ingest import IngestError, ingest_file  # noqa: E402
from app.replay import create_replay_app  # noqa: E402
from tests.make_sample_warc import T0, write_sample  # noqa: E402

CUTOFF = int(T0.timestamp()) + 86400  # T0 + 1 天：future 记录在此之后

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


def replay_path(aid, url):
    return f"/web/{aid}/{CUTOFF}/{quote(url, safe='')}"


def main():
    tmp = tempfile.mkdtemp()
    warc_path = os.path.join(tmp, "sample.warc.gz")
    n = write_sample(warc_path)
    print(f"样例档案 {n} 条记录")

    # ---- 导入 ----
    aid, count = ingest_file(warc_path, "sample.warc.gz")
    check("导入成功", count == n, f"count={count}")

    conn = db.connect()
    row = conn.execute(
        "SELECT ts FROM records WHERE url='http://example.com/home'").fetchone()
    conn.close()
    check("WARC-Date 被保留", row and row["ts"].startswith("2024-01-01"), row["ts"])

    # 损坏档案导入失败
    bad = os.path.join(tmp, "bad.warc.gz")
    with open(bad, "wb") as fh:
        fh.write(os.urandom(500))
    try:
        ingest_file(bad, "bad.warc.gz")
        check("损坏档案导入失败", False, "未抛异常")
    except IngestError as e:
        check("损坏档案导入失败", True, str(e))

    replay = TestClient(create_replay_app())
    admin = TestClient(create_admin_app())

    # ---- 重定向跟随 + HTML 改写 ----
    r = replay.get(replay_path(aid, "http://example.com/"))
    body = r.text
    check("入口 301 跟随到 /home", r.status_code == 200 and "next page" not in body)
    check("横幅含原址", "http://example.com/" in body and "原址" in body)
    check("横幅含命中采集时间", "2024-01-01" in body)
    check("横幅含重定向跳数", "1 跳" in body)
    check("相对 CSS 按 base 解析",
          quote("http://example.com/static/style.css", safe="") in body)
    check("相对图片按 base 解析",
          quote("http://example.com/img/logo.png", safe="") in body)
    check("srcset 改写",
          quote("http://example.com/img/logo@2x.png", safe="") in body)
    check("内联样式查询串保留",
          quote("http://example.com/img/inline-bg.png?size=small", safe="") in body)
    check("脚本绝对地址改写",
          quote("http://example.com/app.js", safe="") in body)
    check("shim 已注入", "__REPLAY_PREFIX__" in body and "XMLHttpRequest" in body)
    check("CSP 禁止外网", "default-src 'none'" in r.headers.get(
        "content-security-policy", ""))

    # ---- CSS 改写：按样式表 URL 解析、保留查询串 ----
    r = replay.get(replay_path(aid, "http://example.com/static/style.css"))
    check("CSS url() 按样式表地址解析",
          quote("http://example.com/static/img/bg.png", safe="") in r.text)
    check("CSS @import 改写",
          quote("http://example.com/static/theme.css", safe="") in r.text)
    check("CSS 资源查询串保留",
          quote("http://example.com/static/fonts/a.woff2?v=1.2", safe="") in r.text)

    # ---- 跨 URL revisit：正文复用，图片仍按该 CSS 地址解析 ----
    r = replay.get(replay_path(aid, "http://example.com/assets/print.css"))
    check("跨 URL revisit 命中", r.status_code == 200)
    check("revisit CSS 图片按该 CSS 地址解析",
          quote("http://example.com/assets/img/bg.png", safe="") in r.text,
          r.text[:200])
    check("revisit 不复用 style.css 的相对基准",
          quote("http://example.com/static/img/bg.png", safe="") not in r.text)

    # ---- 直接取 revisit 基体内容一致 ----
    import re
    from urllib.parse import unquote

    def replay_urls(text):
        pat = re.compile(re.escape(f"/web/{aid}/{CUTOFF}/") + r"([^\"'\s)]+)")
        return sorted(unquote(m) for m in pat.findall(text))

    r1 = replay.get(replay_path(aid, "http://example.com/static/style.css"))
    r2 = replay.get(replay_path(aid, "http://example.com/assets/print.css"))
    u1 = sorted(u.replace("example.com/static/img/", "example.com/assets/img/")
                .replace("example.com/static/theme.css", "example.com/assets/theme.css")
                for u in replay_urls(r1.text))
    u2 = replay_urls(r2.text)
    check("revisit 正文与基体一致（仅基准不同）", u1 == u2, f"{u1} != {u2}")

    # ---- 重定向环 ----
    r = replay.get(replay_path(aid, "http://example.com/loop-a"))
    check("重定向环被检测", r.status_code == 404 and "环" in r.text)

    # ---- 不借未来版本 ----
    r = replay.get(replay_path(aid, "http://example.com/future"))
    check("仅有未来记录 -> 缺档", r.status_code == 404 and "未来" in r.text)

    # ---- revisit 无基体 ----
    r = replay.get(replay_path(aid, "http://example.com/orphan"))
    check("revisit 缺基体 -> 缺档", r.status_code == 404 and "基体" in r.text)

    # ---- 无记录 ----
    r = replay.get(replay_path(aid, "http://example.com/never-captured"))
    check("无记录 -> 缺档", r.status_code == 404 and "无此 URL" in r.text)

    # ---- 带查询串的入口 ----
    r = replay.get(replay_path(aid, "http://example.com/search?q=test"))
    check("查询串入口命中", r.status_code == 200 and "search results" in r.text)

    # ---- 非 GET 阻止 ----
    r = replay.post(replay_path(aid, "http://example.com/submit"))
    check("POST 被 405 阻止", r.status_code == 405)
    r = replay.put(replay_path(aid, "http://example.com/home"))
    check("PUT 被 405 阻止", r.status_code == 405)

    # ---- 普通资源命中 ----
    r = replay.get(replay_path(aid, "http://example.com/img/logo.png"))
    check("图片命中", r.status_code == 200 and r.content[:4] == b"\x89PNG")
    check("命中头含采集时间", r.headers.get("x-archive-date", "").startswith("2024-01-01"))

    # ---- 管理端 API ----
    r = admin.get("/api/archives")
    check("档案列表", any(a["id"] == aid and a["status"] == "ok" for a in r.json()))
    r = admin.get(f"/api/archives/{aid}/urls")
    check("URL 清单", any(u["url"] == "http://example.com/home" for u in r.json()))

    with open(warc_path, "rb") as fh:
        r = admin.post("/api/archives", files={"file": ("sample.warc.gz", fh)})
    check("API 上传导入", r.status_code == 200 and r.json()["records"] == n)
    aid2 = r.json()["id"]

    with open(bad, "rb") as fh:
        r = admin.post("/api/archives", files={"file": ("bad.warc.gz", fh)})
    check("API 损坏档案 400", r.status_code == 400 and "error" in r.json())

    # ---- 缺档清单 ----
    r = admin.get(f"/api/archives/{aid}/log?missing_only=true")
    misses = r.json()
    reasons = " ".join(m["reason"] or "" for m in misses)
    check("缺档清单含环/未来/基体/无记录/非GET",
          all(k in reasons for k in ("环", "未来", "基体", "无此 URL", "非 GET")),
          reasons)
    r = admin.get(f"/api/archives/{aid}/log")
    hits = [x for x in r.json() if x["hit"]]
    check("命中记录含原址与命中时间",
          any(h["requested_url"] == "http://example.com/" and h["hit_ts"]
              for h in hits))

    r = admin.delete(f"/api/archives/{aid2}")
    check("删除档案", r.status_code == 200)

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
