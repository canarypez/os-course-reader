"""按讲保存的对话历史。

一讲一个文件，落在 `DATA_DIR/history/<讲>.json`，最新对话排在前面：

    {"lecture": "<原始 lid>", "conversations": [
        {"id", "title", "created", "updated", "messages": [
            {"role": "user", "content", "page", "ts"},
            {"role": "assistant", "content", "ts", "stopped"?}]}]}

界面偏好之所以要挤进 config.json，是因为 WebView2 隐私模式不留 localStorage；
历史是正经数据，单独开一个目录存，坏了直接删文件就行。

两条要点：

* **文件名不可信**。lid 是用户 clone 下来的仓库目录名，可能带路径分隔符、空格、
  Windows 保留名。做白名单替换 + 截断 + 保留名阻断，并且**在文件里另存一份原始
  lid**，读回来对不上就当作「这讲没有历史」—— 否则两个不同的 lid 一旦归一成同一个
  文件名，就会互相端出对方的对话。
* **只存问答本身，不存组装好的上下文**。每轮要用的课件上下文（全课全貌 + 当前讲
  大纲 + 当前页全文 + 相邻页 + 检索结果）都得按当时所在的页现拼，存进历史既会反复
  撑大请求，也会在用户翻页之后拿旧页的全文去骗模型。引用菜单插进去的
  `> [标签] 原文` 本来就在 content 里，自然跟着走。
"""
import json
import os
import re
import threading
import time
import uuid

import config as cfgmod

#: 一讲最多留多少个对话
MAX_CONVERSATIONS = 40
#: 单个对话最多留多少条消息（一问一答算两条，即 30 轮）
MAX_MESSAGES = 60
#: 单条正文上限（字符）。一次贴进一整篇课件也不至于把文件撑成几 MB
MAX_ANSWER = 20000
MAX_QUESTION = 8000

#: 「读-改-写」串行化。流结束时的落盘和标题生成会撞车，改同一个文件。
_LOCK = threading.Lock()

#: 这些名字（不分大小写、带不带扩展名）在 Windows 上是设备名，建不出文件
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def directory() -> str:
    return os.path.join(cfgmod.DATA_DIR, "history")


def _file_name(lid) -> str:
    """讲 id → 文件名。白名单之外的字符一律换成下划线。"""
    name = re.sub(r"[^\w.-]", "_", str(lid or "")).strip("._") or "lecture"
    name = name[:80]
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    return name + ".json"


def _read(lid):
    """读一整份。没有 / 坏了 / 不属于这一讲，一律返回 None。"""
    path = os.path.join(directory(), _file_name(lid))
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("lecture") != lid:
        return None
    if not isinstance(data.get("conversations"), list):
        return None
    return data


def _write(lid, data):
    """先写 .tmp 再 replace —— 崩在写一半也不会留下一份读不动的 JSON。"""
    d = directory()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, _file_name(lid))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _now() -> float:
    return time.time()


def fallback_title(question) -> str:
    """AI 起名失败时的兜底：问题的前 10 个字。"""
    text = " ".join(str(question or "").split())
    return text[:10] or "新对话"


def _blank(lid):
    return {"lecture": lid, "conversations": []}


def _find(data, cid):
    for c in data["conversations"]:
        if c.get("id") == cid:
            return c
    return None


def _trim(conv):
    """超长的砍掉最旧的。砍的粒度是条，不拆一问一答 —— 拆了会留下孤零零的追问。"""
    msgs = conv.get("messages")
    if isinstance(msgs, list) and len(msgs) > MAX_MESSAGES:
        conv["messages"] = msgs[-MAX_MESSAGES:]


def list_conversations(lid) -> list:
    """本讲的对话列表，最新在前。只给列表要用的字段，不搬正文。"""
    if not lid:
        return []
    with _LOCK:
        data = _read(lid) or _blank(lid)
        out = []
        for c in data["conversations"]:
            msgs = c.get("messages") or []
            out.append({
                "id": c.get("id"),
                "title": c.get("title") or "",
                "created": c.get("created"),
                "updated": c.get("updated"),
                "count": len(msgs),
            })
        return out


def get(lid, cid):
    """整条对话（含全部消息）。找不到返回 None。"""
    if not lid or not cid:
        return None
    with _LOCK:
        data = _read(lid)
        if not data:
            return None
        conv = _find(data, cid)
        return json.loads(json.dumps(conv)) if conv else None


def new(lid, cid=None) -> str:
    """开一个新对话，返回它的 id。

    id 由前端给（crypto.randomUUID）—— 这样界面在第一帧到达之前就能把它选中，
    不必等后端回话。
    """
    cid = str(cid or "").strip() or uuid.uuid4().hex
    if not lid:
        return cid
    with _LOCK:
        data = _read(lid) or _blank(lid)
        if _find(data, cid):
            return cid
        now = _now()
        data["conversations"].insert(0, {
            "id": cid, "title": "", "created": now, "updated": now, "messages": [],
        })
        del data["conversations"][MAX_CONVERSATIONS:]
        _write(lid, data)
    return cid


def ensure_conversation(lid, cid, question, page=None) -> str:
    """流一开始就落一条记录：先把用户的问题写进去，标题用问题前 10 字顶着。

    这样即便回答半路断了、或者用户当场关掉程序，这轮问过什么也还在。
    """
    cid = str(cid or "").strip()
    if not lid or not cid:
        return cid
    with _LOCK:
        data = _read(lid) or _blank(lid)
        conv = _find(data, cid)
        if conv is None:
            now = _now()
            conv = {"id": cid, "title": fallback_title(question),
                    "created": now, "updated": now, "messages": []}
            data["conversations"].insert(0, conv)
            del data["conversations"][MAX_CONVERSATIONS:]
        if not conv.get("title"):
            conv["title"] = fallback_title(question)
        conv["messages"].append({
            "role": "user",
            "content": str(question or "")[:MAX_QUESTION],
            "page": page,                    # 显示用：这条是在哪一页问的
            "ts": _now(),
        })
        conv["updated"] = _now()
        _trim(conv)
        _write(lid, data)
    return cid


def finish_turn(lid, cid, answer, stopped=False) -> bool:
    """把这一轮的回答补上。中途断开也走这里 —— 已经收到的部分要留下。"""
    if not lid or not cid:
        return False
    with _LOCK:
        data = _read(lid) or _blank(lid)
        conv = _find(data, cid)
        if conv is None:
            return False
        msg = {"role": "assistant",
               "content": str(answer or "")[:MAX_ANSWER],
               "ts": _now()}
        if stopped:
            msg["stopped"] = True
        conv["messages"].append(msg)
        conv["updated"] = _now()
        _trim(conv)
        _write(lid, data)
    return True


def set_title(lid, cid, title) -> bool:
    """AI 起好的名字。空白或没这条对话就什么都不做。"""
    title = " ".join(str(title or "").split())[:40]
    if not lid or not cid or not title:
        return False
    with _LOCK:
        data = _read(lid)
        if not data:
            return False
        conv = _find(data, cid)
        if conv is None:
            return False
        conv["title"] = title
        _write(lid, data)
    return True


def delete(lid, cid) -> bool:
    if not lid or not cid:
        return False
    with _LOCK:
        data = _read(lid)
        if not data:
            return False
        before = len(data["conversations"])
        data["conversations"] = [c for c in data["conversations"] if c.get("id") != cid]
        if len(data["conversations"]) == before:
            return False
        _write(lid, data)
    return True


def clear(lid) -> bool:
    """删掉这一讲的全部历史（界面上没入口，留给排查问题时手用）。"""
    if not lid:
        return False
    with _LOCK:
        path = os.path.join(directory(), _file_name(lid))
        if os.path.isfile(path):
            os.remove(path)
            return True
    return False
