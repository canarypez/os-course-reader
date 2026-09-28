"""课程知识库 + LLM 客户端。

把每讲渲染出的 lecture.json（大纲树 + 每页文本）读进内存，作为「整门课全貌」。
提问时构造上下文：当前页全文 + 前后页 + 当前讲大纲 + 整门课各讲标题 + 关键词检索
到的相关页，再交给 OpenAI 兼容接口的模型回答。
"""
import json
import os
import re

import httpx

DEFAULT_SYSTEM = (
    "你是《计算机系统基础》(ICS / CS:APP 风格) 课程的助教，正在陪学生读课件。\n"
    "下面会给你一段课件上下文：包括学生「正在看的那一页」的全文、它前后的页、"
    "本讲的大纲、整门课各讲的结构，以及按问题关键词检索出的相关页。\n"
    "要求：\n"
    "1. 优先依据这些课件内容回答，能结合前后页和整门课的结构来解释，而不是只看当前一页；\n"
    "2. 学生可能刚接触这个主题，讲清楚直觉和来龙去脉，必要时给例子；\n"
    "3. 如果课件上下文不足以回答，明确说明「课件里没有直接讲」，再基于你自己的知识补充；\n"
    "4. 不要编造课件里不存在的页码、公式或结论；\n"
    "5. 数学公式一律用 LaTeX：行内用 $...$、独立成行用 $$...$$，方便前端渲染；\n"
    "6. 用中文回答，条理清晰、重点突出，篇幅适中。"
)


# --------------------------------------------------------------------------- 文本抽取
def _flatten_strings(obj, out):
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _flatten_strings(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _flatten_strings(v, out)


def page_to_text(page: dict) -> str:
    """把一页的 blocks 抽成纯文本，供检索和塞进上下文。"""
    lines = []
    title = page.get("title")
    if title:
        lines.append(title)
    for b in page.get("blocks", []):
        kind = b.get("kind")
        if kind in ("cover", "image", "spacer", "row"):
            continue
        strs = []
        _flatten_strings(b.get("content"), strs)
        text = " ".join(s.strip() for s in strs if s and s.strip())
        if kind == "code" and len(text) > 600:
            text = text[:600]
        if text:
            lines.append(text)
    return "\n".join(lines)


# --------------------------------------------------------------------------- 课程库
class CourseStore:
    def __init__(self, build_root: str):
        self.build_root = build_root
        # id(源目录名) -> {dir, title, tree, pages, page_text}
        self.lectures = {}
        self.reload()

    def reload(self):
        self.lectures = {}
        if not os.path.isdir(self.build_root):
            return
        for name in sorted(os.listdir(self.build_root)):
            lj = os.path.join(self.build_root, name, "lecture.json")
            if not os.path.isfile(lj):
                continue
            try:
                with open(lj, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            title = (data.get("lecture") or {}).get("title") or name
            pages = data.get("pages", [])
            page_text = {}
            for p in pages:
                page_text[p.get("id")] = page_to_text(p)
            self.lectures[name] = {
                "dir": name,
                "title": title,
                "tree": data.get("tree", []),
                "pages": pages,
                "page_text": page_text,
            }

    def lecture_ids(self):
        return list(self.lectures.keys())

    # ---- 大纲 / 结构文本 ----
    def _outline_text(self, nodes, depth=0):
        lines = []
        for node in nodes:
            if node.get("type") == "section":
                lines.append("  " * depth + "▸ " + (node.get("title") or ""))
                lines.extend(self._outline_text(node.get("children") or [], depth + 1))
            else:
                lines.append("  " * depth + "- " + (node.get("title") or ""))
        return lines

    def course_map_text(self):
        lines = []
        for i, lid in enumerate(self.lecture_ids(), 1):
            lec = self.lectures[lid]
            lines.append(f"第{i}讲《{lec['title']}》")
            lines.extend(self._outline_text(lec["tree"], 1))
        return "\n".join(lines) if lines else "（暂无课件，先点右上角「更新」渲染）"

    def outline_text(self, lid):
        lec = self.lectures.get(lid)
        if not lec:
            return ""
        return "\n".join(self._outline_text(lec["tree"]))

    # ---- 检索 ----
    def _tokenize(self, text):
        text = text.lower()
        toks = re.findall(r"[a-z0-9]+", text)          # 英文/数字词
        cjk = re.findall(r"[一-鿿]", text)      # 中文单字
        return toks + cjk

    def retrieve(self, question, exclude_lid=None, k=4):
        q = self._tokenize(question)
        if not q:
            return []
        scored = []
        for lid, lec in self.lectures.items():
            if lid == exclude_lid:
                continue
            for pid, text in lec["page_text"].items():
                low = text.lower()
                s = 0
                for t in q:
                    if t in low:
                        # 英文词命中权重高（更具体）
                        s += 3 if t.isascii() else 1
                if s > 0:
                    scored.append((s, lid, pid, lec["title"]))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]

    def page_by_id(self, lid, pid):
        lec = self.lectures.get(lid)
        if not lec:
            return None
        for p in lec["pages"]:
            if p.get("id") == pid:
                return p
        return None

    def page_index(self, lid, pid):
        lec = self.lectures.get(lid)
        if not lec:
            return None
        for i, p in enumerate(lec["pages"]):
            if p.get("id") == pid:
                return i
        return None


# --------------------------------------------------------------------------- 上下文
def build_context(store: CourseStore, lid, pid, question, max_retrieve):
    lec = store.lectures.get(lid)
    parts = []

    parts.append("【整门课全貌】\n" + store.course_map_text())

    if lec:
        parts.append(f"【当前讲《{lec['title']}》大纲】\n" + store.outline_text(lid))

        idx = store.page_index(lid, pid)
        if idx is not None:
            pages = lec["pages"]
            # 当前页
            cur_title = pages[idx].get("title")
            cur_num = pages[idx].get("number") or (idx + 1)
            parts.append(
                f"【学生正在看：第 {cur_num} 页《{cur_title}》— 全文】\n"
                + lec["page_text"].get(pid, "")
            )
            # 前后页
            for off, label in ((-1, "上一页"), (1, "下一页")):
                j = idx + off
                if 0 <= j < len(pages):
                    pj = pages[j]
                    t = lec["page_text"].get(pj.get("id"), "")
                    if t:
                        parts.append(f"【{label}《{pj.get('title')}》】\n" + t)

    rel = store.retrieve(question, exclude_lid=lid, k=max_retrieve)
    if rel:
        parts.append("【按问题检索到的相关页（其它讲）】")
        for s, rlid, rpid, rtitle in rel:
            t = store.lectures[rlid]["page_text"].get(rpid, "")
            parts.append(f"— 《{rtitle}》第 {rpid} 页：\n{t[:500]}")

    return "\n\n".join(parts)


# --------------------------------------------------------------------------- LLM 调用
def ask(config: dict, store: CourseStore, lid, pid, question):
    api_base = (config.get("api_base") or "").rstrip("/")
    api_key = config.get("api_key") or ""
    model = config.get("model") or "deepseek-chat"
    if not api_base:
        raise RuntimeError("未配置 api_base，请在「设置」里填写。")
    if not api_key:
        raise RuntimeError("未配置 api_key，请在「设置」里填写。")

    ctx = build_context(store, lid, pid, question, int(config.get("max_retrieve", 4)))
    system = config.get("system_prompt") or DEFAULT_SYSTEM

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": ctx + "\n\n【学生的问题】\n" + question},
    ]

    url = api_base + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    resp = httpx.post(url, json=payload, headers=headers, timeout=180.0)
    if resp.status_code >= 400:
        raise RuntimeError(f"API 返回 {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("API 响应格式异常：" + json.dumps(data, ensure_ascii=False)[:300])
