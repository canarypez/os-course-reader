"""更新：git pull + 把每讲重新渲染成网页阅读器 bundle（不导出 PDF，无需 Chrome）。"""
import os
import subprocess

# 窗口化 exe 下，git / python / marp 这些控制台子进程默认会各自弹一个 cmd 窗口；
# CREATE_NO_WINDOW 让它们不带控制台窗口运行（stdout/stderr 仍走管道被我们捕获）。
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _marp_cmd(ppt: str):
    return os.path.join(ppt, "node_modules", ".bin", "marp.cmd")


def update(config: dict, build_root: str, stubs_dir: str, log=lambda line: None):
    """阻塞执行更新，log 回调逐条报告进度。渲染出的 bundle 放到 build_root/<name>/。"""
    repo = config["repo"]
    ppt = os.path.join(repo, "ICS-PPT")
    python = config.get("python") or "python"
    lectures_dir = os.path.join(ppt, "lectures")

    env = dict(os.environ)
    # lecturekit/demo.py 顶层 import fcntl/pty/termios（Unix 专属），用占位模块让 import 过。
    env["PYTHONPATH"] = stubs_dir + os.pathsep + env.get("PYTHONPATH", "")
    marp = _marp_cmd(ppt)
    if os.path.exists(marp):
        env["LECTUREKIT_MARP"] = marp
        log("使用 vendored marp-cli")
    else:
        log("未找到 vendored marp，将回退 npx（可能需联网）")

    # 1. 拉取老师最新课件（失败不致命，用本地版本继续）
    log("git pull --ff-only ...")
    r = subprocess.run(["git", "-C", repo, "pull", "--ff-only"],
                       capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if r.returncode == 0:
        log("git pull 成功")
    else:
        log("git pull 失败（用本地版本继续）: " + (r.stderr.strip().splitlines() or ["?"])[-1][:200])

    # 2. 逐个渲染
    if not os.path.isdir(lectures_dir):
        log("找不到课件目录：" + lectures_dir)
        return
    names = sorted(
        n for n in os.listdir(lectures_dir)
        if os.path.isfile(os.path.join(lectures_dir, n, "lecture.py"))
    )
    if not names:
        log("lectures/ 下没有含 lecture.py 的课件")
        return

    os.makedirs(build_root, exist_ok=True)
    ok = 0
    for n in names:
        src = os.path.join(lectures_dir, n)
        out = os.path.join(build_root, n)
        log(f"渲染 {n} ...")
        r = subprocess.run(
            [python, "-m", "lecturekit.cli", "render", src, "--out", out],
            cwd=ppt, env=env, capture_output=True, text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        if r.returncode == 0:
            ok += 1
            log(f"  {n} 成功")
        else:
            log(f"  {n} 失败：{r.stderr.strip()[-400:]}")
    log(f"完成：{ok}/{len(names)} 个课件渲染成功")
