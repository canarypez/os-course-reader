# ICS 课件阅读器

面向上海交通大学《计算机系统基础》（ICS / CS:APP）课程的本地课件阅读器，附带一个具备课程上下文理解能力的 AI 助教。它将课程仓库（基于 [lecturekit](https://github.com/SJTU-IPADS/OS-Course-Lab) 编写）中的课件渲染为网页幻灯片，并在原生窗口中提供双栏阅读体验：左侧浏览课件，右侧随时就当前页提问。

## 功能特性

- **原生窗口**：以单文件 `.exe` 运行，无需浏览器，界面简洁。
- **双栏阅读**：左侧复用 lecturekit 自带阅读器（大纲 → 点选页面 → 方向键翻页），右侧为常驻对话侧栏；两栏之间的分隔条可以左右拖，宽度记在 `config.json` 里，下次启动照旧。
- **浅色 / 深色**：默认跟随 Windows 的深浅色，设置里也能强制。切换在首次绘制前就生效，不会闪白。
- **页面感知**：提问时自动带上当前页码（界面本身不显示阅读位置）。
- **上下文理解**：回答时注入当前页全文、前后页、本讲大纲、整门课结构，以及按问题关键词检索到的相关页，而非仅依赖单页内容。
- **自动更新**：启动时自动执行 `git pull` 并重新渲染课件，也可通过界面按钮手动触发。
- **可配置 API**：采用 OpenAI 兼容接口，`base_url` / `model` / `api_key` 均可编辑，支持 DeepSeek、OpenAI、Moonshot、智谱、本地 Ollama 等。
- **公式渲染**：助教回答中的 LaTeX 公式（行内 `$...$` 与块级 `$$...$$`）由内置 KaTeX 离线渲染，无需联网。
- **导出 PDF**：一键把当前讲导出为带书签目录的 PDF，走 Windows 原生「另存为」对话框。适合拷到平板或打印。
- **检索模式**：提问栏可在「问答 / 检索」之间切换；检索按关键词在本地翻课件，范围可选「本讲 / 全部」，零成本、零延迟、离线可用，点结果直接跳到对应页。
- **划词引用**：在课件里选中一段文字，**松开鼠标就弹出**菜单，可「引用」（把原文与出处填进提问框）、「解释」（直接就该段发问）或「复制」；在别处左键点一下即消失。公式为预渲染 SVG、无法选中，此时在公式上**右键**会退化为引用该公式的字符。
- **环境检测向导**：检查 git / 课程仓库 / Python / lecturekit / Node / marp / 浏览器七项，逐项显示状态。缺什么补什么——**只有你亲手点下那一项旁边的按钮才会执行安装**，且安装前会显示将要运行的命令。启动时若检测到缺失会自动弹出（可对缺失项选择「不再提示」）。

## 使用方式

### 方式一：直接运行（开发环境）

```powershell
python app.py
```

### 方式二：打包为单文件可执行程序

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
# 产物：dist\ICS-Reader.exe
```

将 `ICS-Reader.exe` 置于任意目录并双击运行。首次启动时，程序会在 exe 同目录生成 `config.json` 与课件渲染产物 `course\`。

## 配置

程序启动后，点击界面右上角「设置」即可修改配置；也可直接编辑 exe 同目录下的 `config.json`。配置项如下：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `repo` | `C:\...\OS-Course` | 课程仓库的本地克隆路径 |
| `api_base` | `https://api.deepseek.com` | OpenAI 兼容接口的根地址，可只填域名（自动补 `/v1/chat/completions`） |
| `model` | `deepseek-chat` | 模型名称 |
| `api_key` | （空） | API 密钥，仅保存在本地 |
| `python` | `python` | 用于渲染课件的 Python 解释器 |
| `auto_update` | `true` | 启动时是否自动更新 |
| `port` | `8787` | 本地 HTTP 服务端口（占用时自动递增） |
| `max_retrieve` | `4` | 提问时额外检索进上下文的相关页数量 |
| `system_prompt` | （空） | 自定义助教提示词，留空则使用默认值 |
| `chat_width` | `440` | 右侧对话栏宽度（px），拖分隔条时自动记录 |
| `theme` | `system` | 界面主题：`system` 跟随 Windows 深浅色，也可填 `light` / `dark` 强制 |
| `wizard_dismissed` | `[]` | 已在向导里选择「不再提示」的缺失项（由程序维护） |

> 界面偏好（`chat_width` / `theme`）只能存在 `config.json` 里：窗口用的是 WebView2 的
> 隐私模式，`localStorage` 不保留。

### 常见 API 组合

> `api_base` 填域名即可，程序会自己补上 `/v1/chat/completions`（先试 `/v1`，不行再退回不带
> `/v1` 的写法）；也可以把完整地址直接粘进来，会原样使用。
>
> 接不上时，设置里有两条自查：
> - **「列出模型」** —— 把该站 `GET /v1/models` 里列出的模型名全抓回来，点一下就填进「模型」框。
>   中转站的模型名常和官方不同（带自己的后缀），照着点比手打靠谱。
> - **「测试连接」** —— 发一条最小请求，把**真实请求的地址**和**对方的原始回复**一并显示出来。
>
> 两个按钮都用输入框里**当前填着的值**，不必先保存。
>
> 设置里的 `API Key` 框回显的是打码后的样子（`••••` + 末四位）。**没重新填就直接点「保存」，
> 程序会沿用原来那把 key，不会把圆点存进去**；要清空 key 就把它删干净再保存。这些圆点是
> 非 ASCII 字符，万一被当成真 key 存下来，请求头会编不出去（报一句看不懂的
> `'ascii' codec can't encode characters`），所以两端都做了拦截。

```jsonc
// DeepSeek（国内直连）
{ "api_base": "https://api.deepseek.com", "model": "deepseek-chat" }

// 各类中转站（one-api / new-api 等）：填站点域名即可
{ "api_base": "https://你的中转站.com", "model": "gpt-4o-mini" }

// OpenAI
{ "api_base": "https://api.openai.com/v1", "model": "gpt-4o" }

// Moonshot（Kimi）
{ "api_base": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k" }

// 智谱 GLM
{ "api_base": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-plus" }

// 本地 Ollama（完全离线）
{ "api_base": "http://localhost:11434/v1", "model": "qwen2.5:7b", "api_key": "ollama" }
```

> `api_key` 仅保存在本地 `config.json` 中，不会被提交或上传到任何地方。

## 工作原理

程序启动后依次完成：

1. 启动本地 HTTP 服务（仅绑定 `127.0.0.1`），提供前端页面、课件静态资源与 JSON API；
2. 后台执行更新：`git pull` 拉取最新课件，并对 `ICS-PPT/lectures/` 下每个包含 `lecture.py` 的目录执行 `lecturekit render`（仅生成网页，不导出 PDF，故无需 Chrome）；
3. 解析每讲渲染出的 `lecture.json`（大纲树 + 每页文本），构建课程知识库；
4. 打开 `pywebview` 原生窗口加载前端。

提问时，后端将以下内容组合为上下文并发送给模型：

1. 整门课各讲、各部分的标题（课程全貌）；
2. 当前讲的大纲；
3. 当前页全文及相邻两页；
4. 按问题关键词在整门课中检索出的相关页。

## 项目结构

```
os-course-reader/
├── app.py            # 入口：启动服务并打开窗口
├── agent.py          # 课程知识库、检索与 LLM 调用
├── sync.py           # 更新：git pull + 重新渲染
├── pdf_export.py     # 导出 PDF 的独立脚本（由外部 Python 子进程执行）
├── wizard.py         # 环境检测与逐项安装
├── server.py         # 本地 HTTP 服务与 JSON API
├── config.py         # 配置读写与路径解析
├── config.json       # 用户配置（本地生成，不提交）
├── web/              # 前端界面（HTML / CSS / JS）
├── stubs/            # Windows 兼容占位模块（fcntl / pty / termios）
├── build_exe.ps1     # PyInstaller 打包脚本
└── README.md
```

## 环境依赖

**浏览课件与提问助教不需要下面任何一项**；只有「更新课件」与「导出 PDF」需要：

| 依赖 | 用途 |
| --- | --- |
| Git | 拉取课程仓库 |
| 课程仓库本地克隆 | 课件源码 |
| Python ≥ 3.12 + `pip install -e ICS-PPT` | 渲染引擎 lecturekit |
| Node.js + vendored Marp CLI | 排版幻灯片 |
| Chrome（或 Edge 兜底） | 打印 PDF 目录页 |

缺哪一项，打开「设置 → 环境检测」，在对应条目旁点「安装」即可（经 `winget` / `pip` / `npm` 安装，安装前会显示将执行的命令）。程序自身的依赖为 `pywebview`、`httpx`；打包另需 `pyinstaller`。

## 注意事项

- 课件渲染本身不导出 PDF，渲染阶段不需要 Chrome；**导出 PDF** 才会用到（Chrome 或 Edge）。
- `git pull` 需要能够访问 GitHub。若网络受限，更新会回退至本地已有版本并在日志中提示，不影响课件浏览。
- 本程序通过本地回环地址提供页面与服务，不对外网开放；仅有 LLM 请求会访问外部 API。
- 向导会真实改动你的系统（安装软件）。它只在你**逐项点击按钮**后执行，绝不自作主张；不想装的话，跳过向导即可正常阅读与提问。
