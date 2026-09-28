"""本地 HTTP 服务：静态资源（前端壳 + 课件 bundle）+ JSON API。

只绑定 127.0.0.1，供本机窗口使用。API：
  GET  /api/course        课程列表 + 大纲 + 是否有 key
  POST /api/chat          {lecture, page, question} -> {answer}
  GET  /api/config        当前配置（key 打码）
  POST /api/config        保存配置
  POST /api/update        触发后台更新
  GET  /api/update/status 更新进度
"""
import json
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import agent
import config as cfgmod
import sync


class App:
    def __init__(self, config):
        self.config = config
        self.store = agent.CourseStore(cfgmod.build_root())
        self._lock = threading.Lock()
        self.update_state = {"running": False, "lines": [], "error": None}
        self.update_thread = None

    # ---- 更新（后台线程）----
    def start_update(self):
        with self._lock:
            if self.update_state["running"]:
                return False
            self.update_state = {"running": True, "lines": [], "error": None}

        def run():
            lines = []
            def log(line):
                lines.append(line)
                with self._lock:
                    self.update_state["lines"] = list(lines)
            try:
                sync.update(
                    self.config, cfgmod.build_root(), cfgmod.resource("stubs"), log
                )
            except Exception as e:
                log("更新出错：" + str(e))
                with self._lock:
                    self.update_state["error"] = str(e)
            finally:
                self.store.reload()  # 渲染完重读 corpus
                with self._lock:
                    self.update_state["running"] = False

        self.update_thread = threading.Thread(target=run, daemon=True)
        self.update_thread.start()
        return True

    def update_status(self):
        with self._lock:
            return dict(self.update_state)

    def course_payload(self):
        lectures = []
        for lid in self.store.lecture_ids():
            lec = self.store.lectures[lid]
            lectures.append({
                "id": lid,
                "title": lec["title"],
                "outline": "\n".join(self.store._outline_text(lec["tree"])),
            })
        return {
            "lectures": lectures,
            "has_key": bool(self.config.get("api_key")),
            "model": self.config.get("model"),
            "auto_update": bool(self.config.get("auto_update")),
        }


def make_server(app: App, port: int):
    app_ref = app
    web_dir = cfgmod.resource("web")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # 静默访问日志

        # ---- helpers ----
        def _json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def _file(self, path):
            # 只允许 web/ 与 build/ 之下的文件，防目录穿越
            if path == "/":
                path = "/index.html"
            rel = path.lstrip("/")
            if rel.startswith("lectures/"):
                rest = rel[len("lectures/"):]
                if "/" not in rest or ".." in rest or "\\" in rest:
                    return self._json({"error": "bad path"}, 400)
                full = os.path.join(cfgmod.build_root(), rest)
            else:
                full = os.path.join(web_dir, rel)
                if os.path.isdir(full):
                    full = os.path.join(full, "index.html")
            full = os.path.normpath(full)
            if not full.startswith(os.path.normpath(web_dir)) and \
               not full.startswith(os.path.normpath(cfgmod.build_root())):
                return self._json({"error": "forbidden"}, 403)
            if not os.path.isfile(full):
                return self._json({"error": "not found"}, 404)
            ctype = "text/html; charset=utf-8"
            if full.endswith(".css"):
                ctype = "text/css; charset=utf-8"
            elif full.endswith(".js"):
                ctype = "application/javascript; charset=utf-8"
            elif full.endswith(".json"):
                ctype = "application/json; charset=utf-8"
            elif full.endswith(".svg"):
                ctype = "image/svg+xml"
            elif full.endswith(".woff2"):
                ctype = "font/woff2"
            with open(full, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # ---- routes ----
        def do_GET(self):
            p = urlparse(self.path).path
            if p == "/api/course":
                return self._json(app_ref.course_payload())
            if p == "/api/config":
                c = dict(app_ref.config)
                c["api_key"] = "••••" + c["api_key"][-4:] if c.get("api_key") else ""
                return self._json(c)
            if p == "/api/update/status":
                return self._json(app_ref.update_status())
            return self._file(p)

        def do_POST(self):
            p = urlparse(self.path).path
            try:
                if p == "/api/chat":
                    b = self._read_json()
                    lid = b.get("lecture")
                    pid = b.get("page")
                    q = (b.get("question") or "").strip()
                    if not q:
                        return self._json({"error": "问题不能为空"}, 400)
                    ans = agent.ask(app_ref.config, app_ref.store, lid, pid, q)
                    return self._json({"answer": ans})
                if p == "/api/config":
                    b = self._read_json()
                    allowed = {k: v for k, v in b.items() if k in cfgmod.DEFAULTS}
                    app_ref.config = cfgmod.save_config(allowed)
                    return self._json({"ok": True, "config": app_ref.course_payload()})
                if p == "/api/update":
                    started = app_ref.start_update()
                    return self._json({"started": started})
                return self._json({"error": "unknown"}, 404)
            except Exception as e:
                traceback.print_exc()
                return self._json({"error": str(e)}, 500)

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return httpd


def find_port(preferred: int):
    import socket
    for port in range(preferred, preferred + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("找不到可用端口")
