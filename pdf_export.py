"""导出某一讲的 PDF —— 独立脚本，由 app 用「外部 python」起子进程调用。

    python pdf_export.py <bundle_dir> <dest_pdf>

为什么是独立脚本：打包后的 exe 是冻结环境，import 不到 lecturekit（它装在课程仓库的
ICS-PPT/ 里、以 pip install -e 暴露给系统 python）。所以照 sync.py 的办法起子进程，
cwd 设在 ICS-PPT。

为什么不用 `lecturekit render --pdf`：那要重新加载课件源码。渲染出的 bundle 里本来就带着
slides.md 与 outline.html（lecturekit 无条件写入、永不删除），直接对 bundle 调 build_deck
即可 —— 省掉一整次源码渲染，也不依赖 lecture.py 还在不在。

依赖：node + vendored marp-cli（跑 slides.md 出 pages.pdf）、Chrome（打印 outline.html）、
pypdf（合并 + 给目录加内链）。缺任何一样都会在这里抛异常，由调用方转成给用户看的提示。
"""
import shutil
import sys
from pathlib import Path

#: build_deck 会把 PDF 写进 bundle 目录，用这个名字产出、随后搬走。
TMP_NAME = "_export_tmp"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if len(sys.argv) != 3:
        print("用法: python pdf_export.py <bundle_dir> <dest_pdf>", file=sys.stderr)
        return 2

    bundle = Path(sys.argv[1]).resolve()
    dest = Path(sys.argv[2]).resolve()

    for need in ("slides.md", "outline.html"):
        if not (bundle / need).is_file():
            print(f"bundle 里缺少 {need}，无法导出（先点「更新」重新渲染一次）", file=sys.stderr)
            return 3

    from lecturekit.renderers.viewer.marp import build_deck

    # 只传 "pdf"：不重建 slides.html，避免动到正在阅读的那份 HTML 产物。
    build_deck(bundle, ("pdf",), name=TMP_NAME)

    produced = bundle / (TMP_NAME + ".pdf")
    if not produced.is_file():
        print("build_deck 没有产出 PDF", file=sys.stderr)
        return 4

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(produced), str(dest))
    print("OK:" + str(dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
