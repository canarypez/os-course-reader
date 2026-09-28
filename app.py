"""课件阅读器 + AI 助教 —— 入口。

启动本地 HTTP 服务（前端壳 + 课件 bundle + API），再用 pywebview 开一个原生窗口。
用法：python app.py    （打包：build_exe.ps1）
"""
import threading

import config as cfgmod
import server


def main():
    config = cfgmod.load_config()
    app = server.App(config)

    port = server.find_port(int(config.get("port") or 8787))
    httpd = server.make_server(app, port)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    # 启动时自动更新（后台线程，不阻塞窗口）
    if config.get("auto_update"):
        app.start_update()

    import webview
    url = f"http://127.0.0.1:{port}/"
    webview.create_window(
        "计算机系统基础 · 阅读器",
        url,
        width=1440,
        height=900,
        min_size=(960, 640),
    )
    webview.start()


if __name__ == "__main__":
    main()
