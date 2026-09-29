"""本地 HTTP 服务：静态资源（前端壳 + 课件 bundle）+ JSON API。

只绑定 127.0.0.1，供本机窗口使用。API：
  GET  /api/course           课程列表 + 大纲 + 是否有 key
  POST /api/chat             {lecture, page, question} -> {answer}
  POST /api/test_api         {api_base, model, api_key} -> 连接自检结果
  POST /api/models           {api_base, api_key} -> 该站可用的模型 id 列表
  POST /api/search           {query, lecture|null} -> {results}
  POST /api/export_pdf       {lecture, dest} -> 启动后台导出
  GET  /api/export/status    导出进度
  POST /api/reveal           {path} -> 在资源管理器里定位该文件
  GET  /api/wizard           环境检测结果
  POST /api/wizard/install   {key, dir?} -> 启动后台安装
  GET  /api/wizard/status    安装进度
  GET  /api/config           当前配置（key 打码）
  POST /api/config           保存配置
  POST /api/update           触发后台更新
  GET  /api/update/status    更新进度
  POST /api/chat/stream      {lecture, page, question, cid, history} -> SSE 流式回答
  POST /api/history/list     {lecture} -> 本讲的对话列表
  POST /api/history/new      {lecture, cid?} -> 新对话 id
  POST /api/history/get      {lecture, id} -> 整条对话
  POST /api/history/delete   {lecture, id} -> 删掉一条对话
"""
import json
import os
import subprocess
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import agent
import config as cfgmod
import history as hist
import sync
import wizard


class Job:
    """一个后台任务的状态：running + 逐行日志 + 结果 / 错误。

    更新、导出 PDF、装依赖三件事的形状完全一样（慢、要报进度、要能轮询），
    共用这一个类，别再抄三份。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.lines = []
        self.error = None
        self.result = None

    def snapshot(self):
        with self._lock:
            return {
                "running": self.running,
                "lines": list(self.lines),
                "error": self.error,
                "result": self.result,
            }

    def _log(self, line):
        with self._lock:
            self.lines.append(str(line))

    def start(self, work, name="job"):
        """work(log) 在后台线程里跑；已在跑则返回 False（不排队、不重入）。"""
        with self._lock:
            if self.running:
                return False
            self.running = True
            self.lines = []
            self.error = None
            self.result = None

        def run():
            try:
                result = work(self._log)
                with self._lock:
                    self.result = result
            except Exception as e:
                traceback.print_exc()
                with self._lock:
                    self.error = str(e)
            finally:
                with self._lock:
                    self.running = False

        threading.Thread(target=run, daemon=True, name=name).start()
        return True


class App:
    def __init__(self, config):
        self.config = config
        self.store = agent.CourseStore(cfgmod.build_root())
        self.update_job = Job()
        self.export_job = Job()
        self.install_job = Job()

    # ---- 更新 ----
    def start_update(self):
        def work(log):
            sync.update(self.config, cfgmod.build_root(), cfgmod.resource("stubs"), log)
            self.store.reload()   # 渲染完重读 corpus

        return self.update_job.start(work, name="update")

    # ---- 导出 PDF ----
    def start_export(self, lid, dest):
        bundle = os.path.join(cfgmod.build_root(), lid)
        if not os.path.isdir(bundle):
            return False
        if not dest:
            # 浏览器直开（没有 pywebview 的原生「另存为」）时的落点
            dest = os.path.join(cfgmod.DATA_DIR, "exports", lid + ".pdf")
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        repo = self.config.get("repo") or ""
        ppt = os.path.join(repo, "ICS-PPT")
        python = (self.config.get("python") or "python").strip()
        script = cfgmod.resource("pdf_export.py")

        def work(log):
            env = sync.render_env(ppt, cfgmod.resource("stubs"))
            env["PYTHONIOENCODING"] = "utf-8"   # 目标路径可能含中文
            log("正在生成 PDF（marp 重排 + 合并目录，需要几十秒）…")
            r = subprocess.run(
                [python, script, bundle, dest],
                cwd=ppt, env=env, capture_output=True, text=True, encoding="utf-8",
                errors="replace", creationflags=sync.CREATE_NO_WINDOW,
            )
            tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-6:]
            for line in tail:
                log(line)
            if r.returncode != 0:
                raise RuntimeError(self._export_error(r.returncode, tail))
            if not os.path.isfile(dest):
                raise RuntimeError("导出结束但没找到文件：" + dest)
            log("导出完成")
            return {"path": dest}

        return self.export_job.start(work, name="export")

    @staticmethod
    def _export_error(code, tail):
        text = " ".join(tail)
        if "Chrome" in text or "chrome" in text or code == 1 and "Browser" in text:
            return "找不到 Chrome，无法排版目录页。可在「环境检测」里安装 Chrome，或用 Edge 兜底。"
        if "marp" in text.lower() or "npx" in text:
            return "marp 不可用（课件排版工具）。请在「环境检测」里安装 marp-cli。"
        if "lecturekit" in text or "ModuleNotFoundError" in text:
            return "渲染环境不完整（lecturekit 未装好）。请打开「环境检测」逐项补全。"
        return f"导出失败（退出码 {code}）：{text[-200:]}"

    # ---- 环境检测 / 安装 ----
    def wizard_payload(self):
        items = wizard.detect(self.config)
        s = wizard.summary(items)
        dismissed = set(self.config.get("wizard_dismissed") or [])
        return {
            "items": items,
            "ok": s["ok"],
            "missing": s["missing"],
            # 启动时自动弹窗的条件：确实缺东西，且没有一项是用户说过不再提示的
            "should_prompt": bool(s["missing"]) and not all(m in dismissed for m in s["missing"]),
        }

    def start_install(self, key, target_dir=None):
        def work(log):
            res = wizard.install(key, self.config, log, target_dir=target_dir)
            if res.get("config"):
                # 装完把解析到的绝对路径（python / repo）写回配置，否则下次还是找不到
                self.config = cfgmod.save_config(res["config"])
            if not res.get("ok"):
                raise RuntimeError("安装未成功，详见上方输出。")
            return res

        return self.install_job.start(work, name="install")

    # ---- 课程列表 ----
    def course_payload(self):
        lectures = []
        for lid in self.store.lecture_ids():
            lec = self.store.lectures[lid]
            lectures.append({
                "id": lid,
                "vid": lec["vid"],
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

        def _sse(self, obj):
            """发一帧 SSE。写失败（客户端断开）由调用方接住。

            wbufsize 是 0，wfile.write 直接就是 sendall，写入路径上没有缓冲；
            flush 留着当保险。
            """
            self.wfile.write(
                ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()

        def _chat_stream(self):
            """流式问答。

            整个分支待在 do_POST 的 try **之外**：一旦发过 200 和一堆帧，再让那个
            `except -> _json(500)` 去 send_response，就是二次发状态行，报出来的东西
            比原始错误还难看。所以这里自己兜住一切。
            """
            try:
                b = self._read_json()
            except Exception:
                return self._json({"error": "请求体不是合法 JSON"}, 400)

            lid = b.get("lecture")
            pid = b.get("page")
            q = (b.get("question") or "").strip()
            cid = str(b.get("cid") or "").strip()
            history = b.get("history") or []
            if not q:
                return self._json({"error": "问题不能为空"}, 400)

            # 先建生成器并拉第一帧，把「没填 key」「地址不对」「401」这些在**发响应头
            # 之前**逼出来 —— 那会儿还能规规矩矩回一个 JSON 错误。
            gen = agent.stream_chat(app_ref.config, app_ref.store, lid, pid, q, history)
            try:
                first = next(gen)
            except StopIteration:
                first = None
            except Exception as e:
                return self._json({"error": str(e)}, 400)

            # 到这儿请求已经通了，发头。之后不写 Content-Length —— 靠连接关闭定界，
            # 这正是 SSE 要的。实测 WebView2 会逐块交给 fetch 的 ReadableStream。
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            tracked = bool(lid) and bool(cid)      # 没有当前讲就没处显示，不记
            if tracked:
                hist.ensure_conversation(lid, cid, q, page=pid)

            parts = []
            aborted = False
            try:
                self._sse({"type": "meta", "cid": cid})
                if first:
                    parts.append(first)
                    self._sse({"type": "delta", "content": first})

                for text in gen:
                    parts.append(text)
                    self._sse({"type": "delta", "content": text})
            except OSError:
                aborted = True                 # 客户端没了：关窗口 / 切讲 / 点了停止
            except Exception as e:
                aborted = True
                try:
                    self._sse({"type": "error", "error": str(e)})
                except OSError:
                    pass

            answer = "".join(parts)
            if tracked and answer:
                hist.finish_turn(lid, cid, answer, stopped=aborted)

            if aborted:
                return                         # 连接已经没了，再写只是继续抛

            self._sse({"type": "done"})

            # 标题帧在 done **之后**，前端必须一直读到 EOF 才收得到。生成失败就不发，
            # 列表里那条用问题前 10 字的兜底标题照样能用。
            title = agent.generate_title(app_ref.config, q, answer)
            if title and tracked:
                hist.set_title(lid, cid, title)
            if title:
                try:
                    self._sse({"type": "title", "title": title})
                except OSError:
                    pass


        def _read_json(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def _probe_config(self):
            """用界面上「当前填着」的值去探测，不必先保存。

            测试连接 / 列模型都吃这一套，所以放一处。
            """
            b = self._read_json()
            probe = dict(app_ref.config)
            # base / model 原样照收：界面清空了就是清空了，不能偷偷回退到存着的值，
            # 否则用户会看到「地址」和「实际打的地址」对不上
            for k in ("api_base", "model"):
                if k in b:
                    probe[k] = str(b[k]).strip()
            # key 不同：界面上回显的是打码后的（••••1234），没重填就用存着的那把
            key = str(b.get("api_key") or "").strip()
            if key and not cfgmod.is_masked(key):
                probe["api_key"] = key
            return probe

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
            elif full.endswith(".png"):
                ctype = "image/png"
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
                c["api_key"] = cfgmod.MASK + c["api_key"][-4:] if c.get("api_key") else ""
                return self._json(c)
            if p == "/api/update/status":
                return self._json(app_ref.update_job.snapshot())
            if p == "/api/export/status":
                return self._json(app_ref.export_job.snapshot())
            if p == "/api/wizard":
                return self._json(app_ref.wizard_payload())
            if p == "/api/wizard/status":
                return self._json(app_ref.install_job.snapshot())
            return self._file(p)

        def do_POST(self):
            p = urlparse(self.path).path
            # 流式必须先走：它自己发响应头，不能落进下面那个 except -> _json(500) 里
            if p == "/api/chat/stream":
                return self._chat_stream()
            try:
                if p == "/api/chat":
                    b = self._read_json()
                    lid = b.get("lecture")
                    pid = b.get("page")
                    q = (b.get("question") or "").strip()
                    if not q:
                        return self._json({"error": "问题不能为空"}, 400)
                    ans = agent.ask(app_ref.config, app_ref.store, lid, pid, q,
                                    b.get("history"))
                    return self._json({"answer": ans})
                if p.startswith("/api/history/"):
                    b = self._read_json()
                    lid = b.get("lecture")
                    if not lid:
                        return self._json({"error": "缺少 lecture"}, 400)
                    cid = b.get("id") or b.get("cid")
                    if p == "/api/history/list":
                        return self._json({"conversations": hist.list_conversations(lid)})
                    if p == "/api/history/new":
                        return self._json({"id": hist.new(lid, cid)})
                    if p == "/api/history/get":
                        return self._json({"conversation": hist.get(lid, cid)})
                    if p == "/api/history/delete":
                        return self._json({"ok": hist.delete(lid, cid)})
                if p in ("/api/test_api", "/api/models"):
                    probe = self._probe_config()
                    if p == "/api/test_api":
                        return self._json(agent.test_connection(probe))
                    return self._json(agent.list_models(probe))
                if p == "/api/search":
                    b = self._read_json()
                    q = (b.get("query") or "").strip()
                    if not q:
                        return self._json({"results": []})
                    lid = b.get("lecture") or None
                    scope = b.get("scope") or "lecture"
                    if scope == "all":
                        lid = None
                    return self._json({"results": app_ref.store.search(q, lid=lid, k=30)})
                if p == "/api/export_pdf":
                    b = self._read_json()
                    lid = b.get("lecture")
                    dest = (b.get("dest") or "").strip()
                    if not lid:
                        return self._json({"error": "缺少 lecture"}, 400)
                    if dest and not dest.lower().endswith(".pdf"):
                        dest += ".pdf"
                    if not os.path.isdir(os.path.join(cfgmod.build_root(), lid)):
                        return self._json({"error": "这一讲还没渲染出课件，先点右上角「更新」"}, 400)
                    if not app_ref.start_export(lid, dest):     # dest 为空则由后端兜底
                        return self._json({"error": "已有导出在进行"}, 400)
                    return self._json({"started": True})
                if p == "/api/reveal":
                    b = self._read_json()
                    path = (b.get("path") or "").strip()
                    if path and os.path.exists(path):
                        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)],
                                         creationflags=sync.CREATE_NO_WINDOW)
                    return self._json({"ok": True})
                if p == "/api/wizard/install":
                    b = self._read_json()
                    key = b.get("key")
                    if not key:
                        return self._json({"error": "缺少 key"}, 400)
                    started = app_ref.start_install(key, b.get("dir"))
                    return self._json({"started": started})
                if p == "/api/config":
                    b = self._read_json()
                    allowed = {k: v for k, v in b.items() if k in cfgmod.DEFAULTS}
                    # api_key 若是界面上那串掩码，save_config 会丢掉它 —— 保存掩码会把
                    # 真 key 覆盖成「••••1234」，之后每个请求都发不出去
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
