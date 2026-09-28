"""配置读写 + 路径解析。

api_base / model / api_key 全部可编辑（OpenAI 兼容接口），写在 exe 同目录的
config.json 里；界面「设置」里也能改。打包成 exe 后，config.json 和渲染产物
build/ 都放在 exe 旁边（DATA_DIR），可写；代码和前端资源打进 exe 内部。
"""
import json
import os
import sys

# 是否被 PyInstaller 打包
FROZEN = bool(getattr(sys, "frozen", False))

# 源码目录（开发时）
APP_DIR = os.path.dirname(os.path.abspath(__file__))

# 可写数据目录：打包后 = exe 所在目录；开发时 = 本源码目录
DATA_DIR = os.path.dirname(sys.executable) if FROZEN else APP_DIR

# 打包后的只读资源目录（onefile 解压临时目录）
def resource(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", APP_DIR)
    return os.path.join(base, rel)


DEFAULTS = {
    # 老师仓库的本地 clone
    "repo": r"C:\Users\lenovo\Downloads\CSAPP\OS-Course",
    # OpenAI 兼容接口：deepseek / openai / moonshot / 智谱 / ollama 等都能填
    "api_base": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-chat",
    # 用哪个 python 解释器去渲染课件（打包后 exe 本身不是解释器）
    "python": "python",
    # 启动时自动 git pull + 重新渲染
    "auto_update": True,
    # 本地 HTTP 端口（被占用会自动 +1）
    "port": 8787,
    # 检索时额外塞进上下文的相关页数量
    "max_retrieve": 4,
    # 自定义 system prompt（留空用默认）
    "system_prompt": "",
}


def config_path() -> str:
    return os.environ.get("OSCOURSE_CONFIG", os.path.join(DATA_DIR, "config.json"))


def build_root() -> str:
    # 用 course/ 而不是 build/，避免和 PyInstaller 的构建缓存目录撞名
    return os.path.join(DATA_DIR, "course")


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    p = config_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(updates: dict) -> dict:
    cfg = load_config()
    cfg.update({k: v for k, v in updates.items() if k in DEFAULTS})
    p = config_path()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg
