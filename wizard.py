"""首次运行向导：检测渲染 / 导出 PDF 所需的工具链，缺什么补什么。

两条纪律：
1. 本模块**自己不做任何安装**。`detect()` 只读；真正的安装只在用户在界面上逐项点击后，
   由 `install()` 执行 —— winget 会真的改动用户的系统，不能替用户决定。
2. 检测顺序有意义（没 git 就 clone 不了仓库，没仓库就测不了 lecturekit），
   所以 `detect()` 按依赖顺序返回，并且上游缺失时下游标为「跳过」而不是「失败」。

与 sync.py 的分工：sync.py 负责「知道怎么渲染」，本模块负责「知道能不能渲染」。
"""
import os
import re
import shutil
import subprocess

from sync import CREATE_NO_WINDOW, marp_js

#: 课件仓库（向导可代为 clone，浅克隆省流量）
COURSE_REPO = "https://github.com/SJTU-IPADS/OS-Course-Lab.git"

#: winget 包 ID —— 均已在本机核实存在
WINGET_IDS = {
    "git": "Git.Git",
    "python": "Python.Python.3.12",
    "node": "OpenJS.NodeJS.LTS",
    "chrome": "Google.Chrome",
}

#: Chrome 的常见安装位置；与 lecturekit 的 pdf.find_chrome() 保持一致，
#: 再加上 Edge 作为兜底（Windows 必有，能用来打印 outline.html）。
CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)
EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def _run(cmd, cwd=None, timeout=25, env=None):
    """跑一条探测命令；失败不抛异常，返回 (ok, 输出)。"""
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                           timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    out = (r.stdout or "").strip() or (r.stderr or "").strip()
    return r.returncode == 0, out


def _first_line(text: str, limit: int = 80) -> str:
    return (text.strip().splitlines() or [""])[0][:limit]


def find_chrome() -> str | None:
    """Chrome 可执行文件；没有就返回 None。与 lecturekit 的查找顺序一致。"""
    for var in ("CHROME_PATH", "CHROME_BIN"):
        p = os.environ.get(var)
        if p and os.path.isfile(p):
            return p
    for p in CHROME_PATHS:
        if os.path.isfile(p):
            return p
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def find_edge() -> str | None:
    for p in EDGE_PATHS:
        if os.path.isfile(p):
            return p
    return None


def marp_version(repo: str) -> str | None:
    """从 lecturekit 源码里读出它固定的 marp 版本（prepare.sh 用 sed 读的是同一行）。

    走读文件而不是 import：向导跑在冻结的 exe 里，import 不到 lecturekit。
    """
    path = os.path.join(repo, "ICS-PPT", "lecturekit", "renderers", "viewer", "marp.py")
    try:
        with open(path, "r", encoding="utf-8") as f:
            m = re.search(r'^MARP_VERSION = "([^"]+)"', f.read(), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


# --------------------------------------------------------------------------- 检测
def detect(config: dict) -> list[dict]:
    """按依赖顺序检测，返回 [{key, label, ok, skip, detail, action}]。

    action 描述「怎么修」，由界面上那颗按钮触发；kind 有 winget / pip / npm / clone。
    """
    repo = (config.get("repo") or "").strip()
    python = (config.get("python") or "python").strip()
    ppt = os.path.join(repo, "ICS-PPT") if repo else ""
    items = []

    def add(key, label, ok, detail, action=None, skip=False):
        items.append({"key": key, "label": label, "ok": ok, "skip": skip,
                      "detail": detail, "action": action})

    # 1. git
    ok, out = _run(["git", "--version"])
    add("git", "Git", ok, _first_line(out) if ok else "未找到 git",
        {"kind": "winget", "id": WINGET_IDS["git"]})

    # 2. 课程仓库
    if ok:
        has_repo = bool(ppt) and os.path.isdir(os.path.join(ppt, "lectures"))
        add("repo", "课程仓库", has_repo,
            repo if has_repo else (f"{repo} 下没有 ICS-PPT/lectures" if repo else "未设置"),
            {"kind": "clone", "url": COURSE_REPO})
    else:
        add("repo", "课程仓库", False, "需要先装 git", None, skip=True)

    # 3. python 解释器
    py_ok, py_out = _run([python, "-c", "import sys; print(sys.version.split()[0])"])
    add("python", "Python", py_ok, _first_line(py_out) if py_ok else f"跑不起来：{python}",
        {"kind": "winget", "id": WINGET_IDS["python"]})

    # 4. lecturekit（渲染与导出 PDF 都要）
    if py_ok and os.path.isdir(ppt):
        lk_ok, lk_out = _run([python, "-c", "import lecturekit; print(lecturekit.__name__)"],
                             cwd=ppt)
        add("lecturekit", "lecturekit（课件渲染引擎）", lk_ok,
            "已安装" if lk_ok else _first_line(lk_out) or "未安装",
            {"kind": "pip", "target": os.path.join(ppt)})
    else:
        add("lecturekit", "lecturekit（课件渲染引擎）", False,
            "需要先有 Python 与课程仓库", None, skip=True)

    # 5. node
    node_ok, node_out = _run(["node", "--version"])
    add("node", "Node.js（marp 运行时）", node_ok,
        _first_line(node_out) if node_ok else "未找到 node",
        {"kind": "winget", "id": WINGET_IDS["node"]})

    # 6. vendored marp
    if node_ok and ppt:
        ver = marp_version(repo)
        has_marp = os.path.isfile(marp_js(ppt))
        add("marp", "marp-cli（课件排版）", has_marp,
            f"已 vendored {ver or ''}".strip() if has_marp
            else ("未安装到 ICS-PPT/node_modules" if ver else "读不到 MARP_VERSION"),
            {"kind": "npm", "cwd": ppt, "version": ver})
    else:
        add("marp", "marp-cli（课件排版）", False, "需要先有 Node.js 与课程仓库", None, skip=True)

    # 7. 浏览器（只有导出 PDF 需要）
    chrome = find_chrome()
    edge = find_edge()
    if chrome:
        add("chrome", "浏览器（导出 PDF 用）", True, chrome, None)
    elif edge:
        add("chrome", "浏览器（导出 PDF 用）", True, edge + "（Edge 兜底）", None)
    else:
        add("chrome", "浏览器（导出 PDF 用）", False, "未找到 Chrome / Edge",
            {"kind": "winget", "id": WINGET_IDS["chrome"]})

    return items


def summary(items: list[dict]) -> dict:
    real = [i for i in items if not i["skip"]]
    return {
        "ok": all(i["ok"] for i in real),
        "missing": [i["key"] for i in real if not i["ok"]],
    }


# --------------------------------------------------------------------------- 安装
def _winget_install(pkg_id: str, log):
    log(f"winget install --id {pkg_id} …")
    ok, out = _run(
        ["winget", "install", "--id", pkg_id, "--exact", "--silent",
         "--accept-package-agreements", "--accept-source-agreements"],
        timeout=1800,
    )
    log(_first_line(out, 200) if out else ("winget 结束" if ok else "winget 失败"))
    return ok


def _pip_install(target: str, config: dict, log):
    python = (config.get("python") or "python").strip()
    log(f"{python} -m pip install -e {target} …")
    ok, out = _run([python, "-m", "pip", "install", "-e", target], cwd=target, timeout=1800)
    log(_first_line(out, 200) if out else ("pip 结束" if ok else "pip 失败"))
    return ok


def _npm_install(cwd: str, version: str | None, log):
    if not version:
        log("读不到 MARP_VERSION，跳过")
        return False
    log(f"npm install @marp-team/marp-cli@{version} …")
    ok, out = _run(["npm", "install", "--no-audit", "--no-fund",
                    f"@marp-team/marp-cli@{version}"], cwd=cwd, timeout=1800)
    log(_first_line(out, 200) if out else ("npm 结束" if ok else "npm 失败"))
    return ok


def _git_clone(url: str, dest: str, log):
    log(f"git clone --depth 1 {url} {dest} …")
    ok, out = _run(["git", "clone", "--depth", "1", url, dest], timeout=1800)
    log(_first_line(out, 200) if out else ("clone 完成" if ok else "clone 失败"))
    return ok


def _resolve_python() -> str | None:
    """winget 装完 Python 后本进程的 PATH 不会更新，得去常见位置把绝对路径捞出来。"""
    cands = []
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        base = os.path.join(local, "Programs", "Python")
        if os.path.isdir(base):
            for name in sorted(os.listdir(base), reverse=True):
                cands.append(os.path.join(base, name, "python.exe"))
    for name in ("python", "python3"):
        p = shutil.which(name)
        if p:
            cands.append(p)
    for p in cands:
        if os.path.isfile(p):
            ok, _ = _run([p, "-c", "import sys"])
            if ok:
                return p
    return None


def _resolve_node_dir() -> str | None:
    """同理：node 装完后把 C:\\Program Files\\nodejs 加进本进程 PATH，npm 才找得到。"""
    for d in (r"C:\Program Files\nodejs", r"C:\Program Files (x86)\nodejs"):
        if os.path.isfile(os.path.join(d, "node.exe")):
            return d
    p = shutil.which("node")
    return os.path.dirname(p) if p else None


def install(key: str, config: dict, log, target_dir: str | None = None) -> dict:
    """执行某一项的安装。返回 {'ok': bool, 'config': {...要写回的配置...}}。

    只有用户在界面上点了那一项才会被调用。
    """
    repo = (config.get("repo") or "").strip()
    ppt = os.path.join(repo, "ICS-PPT") if repo else ""
    update = {}

    if key == "git":
        ok = _winget_install(WINGET_IDS["git"], log)
    elif key == "python":
        ok = _winget_install(WINGET_IDS["python"], log)
        if ok:
            p = _resolve_python()
            if p:
                update["python"] = p
                log("Python 解释器：" + p)
            else:
                log("装完了但没找到解释器，可能需要重开一次程序")
    elif key == "node":
        ok = _winget_install(WINGET_IDS["node"], log)
        if ok:
            d = _resolve_node_dir()
            if d:
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                log("已把 node 目录加进本次运行的环境：" + d)
            else:
                log("装完了但没找到 node，可能需要重开一次程序")
    elif key == "chrome":
        ok = _winget_install(WINGET_IDS["chrome"], log)
    elif key in ("clone", "repo"):     # 界面按检测项的 key（repo）调用，两种都收
        if not target_dir:
            log("没有指定克隆目录")
            return {"ok": False, "config": update}
        ok = _git_clone(COURSE_REPO, target_dir, log)
        if ok:
            update["repo"] = target_dir
            log("课程仓库：" + target_dir)
    elif key == "lecturekit":
        ok = _pip_install(ppt, config, log) if ppt else False
    elif key == "marp":
        ok = _npm_install(ppt, marp_version(repo), log) if ppt else False
    else:
        log("未知的检测项：" + key)
        ok = False

    return {"ok": bool(ok), "config": update}
