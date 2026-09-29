# ICS 课件阅读器

面向上海交通大学《计算机系统基础》（ICS / CSAPP）课程的本地课件阅读器，附带一个具备课程上下文理解能力的 AI 助手窗口。它将课程仓库（基于 [lecturekit](https://github.com/SJTU-IPADS/OS-Course-Lab) 编写）中的课件渲染为网页幻灯片，并在原生窗口中提供双栏阅读体验：左侧浏览课件，右侧随时就当前页提问。

## 主要功能

- 以单文件 `.exe` 运行。左侧复用 lecturekit 自带阅读器，右侧为对话侧栏；两栏之间的分隔条可拖动，宽度记录在 `config.json` 。
- 默认跟随 Windows 的深浅色，设置里可以切换。
- 回答时注入当前页全文、前后页、本讲大纲、整门课结构，以及按问题关键词检索到的相关页，而非仅依赖单页内容。
- 启动时自动执行 `git pull` 并重新渲染课件，“更新”按钮可手动触发。
- 采用 OpenAI 兼容接口，`base_url` / `model` / `api_key` 均可编辑，支持 DeepSeek、OpenAI、Moonshot、智谱、本地 Ollama 等。
- 标题、粗体/斜体、删除线、有序与无序列表、表格、引用块、分隔线、链接、行内与块级代码。渲染器内置极简实现，不引 CDN、无第三方库。LaTeX 公式由内置 KaTeX 离线渲染。默认流式输出实时渲染。
- 对话**按讲分开**；可以随时「新对话」。
- 可以将课件一键以PDF格式导出。
- 提问栏可在「问答 / 检索」之间切换；检索范围为「本讲 / 全部」。
- 选中文字，**松开鼠标弹出**菜单，包含「引用」、「解释」、「复制」。

## 使用方式

### 方式一：可执行文件（推荐）

 [Releases](https://github.com/canarypez/os-course-reader/releases) 下载最新 `ICS-Reader.exe`，仅 Windows。

### 方式二：直接运行

```powershell
python app.py
```

### 方式三：打包为单文件可执行程序

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
# 产物：dist\ICS-Reader.exe
```

将 `ICS-Reader.exe` 置于任意目录并双击运行。首次启动时，程序会在 exe 同目录生成 `config.json`
与课件渲染产物 `course\`；开始提问后，对话历史会写在同目录的 `history\<讲>.json` 里。
`history\` 和 `course\` 都可以随时整个删掉，不影响程序运行（前者只是聊天记录，后者下次更新会重建）。

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
| `port` | `8787` | 本地 HTTP 服务端口 |
| `max_retrieve` | `4` | 提问时额外检索进上下文的相关页数量 |
| `system_prompt` | （空） | 自定义提示词，留空则使用默认值 |
| `chat_width` | `440` | 右侧对话栏宽度（px），拖分隔条时自动记录 |
| `theme` | `system` | 界面主题：`system` 跟随 Windows 深浅色，也可填 `light` / `dark` 强制 |
| `history_budget` | `12000` | 追问时最多往回带多少个字符的旧消息 |
| `wizard_dismissed` | `[]` | 已在向导里选择「不再提示」的缺失项 |

> 界面偏好（`chat_width` / `theme`）只能存在 `config.json` 里：窗口使用 WebView2 
> 隐私模式，`localStorage` 不保留。

### 常见 API 问题

> `api_base` 填写域名，程序自动补上 `/v1/chat/completions`；
> 连接不上时，请在设置自查：
> - **「列出模型」** —— 获取该站 `GET /v1/models` 里列出的模型。
> - **「测试连接」** —— 发一条最小请求，显示**真实请求的地址**和**对方的原始回复**。

```jsonc
// DeepSeek
{ "api_base": "https://api.deepseek.com", "model": "deepseek-chat" }

// 各类中转站（one-api / new-api 等）：站点域名
{ "api_base": "https://中转站.com", "model": "gpt-4o-mini" }

// OpenAI
{ "api_base": "https://api.openai.com/v1", "model": "gpt-4o" }

// Moonshot（Kimi）
{ "api_base": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k" }

// 智谱 GLM
{ "api_base": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-plus" }

// 本地 Ollama
{ "api_base": "http://localhost:11434/v1", "model": "qwen2.5:7b", "api_key": "ollama" }
```

> `api_key` 仅保存在本地 `config.json` 中，不会被提交或上传到任何地方。

## 工作原理

程序启动后依次完成：

1. 启动本地 HTTP 服务（仅绑定 `127.0.0.1`），提供前端页面、课件静态资源与 JSON API；
2. 后台执行更新：`git pull` 拉取最新课件，并对 `ICS-PPT/lectures/` 下每个包含 `lecture.py` 的目录执行 `lecturekit render`；
3. 解析每讲渲染出的 `lecture.json`，构建课程知识库；
4. 打开 `pywebview` 原生窗口加载前端。

提问时，后端将以下内容组合为上下文并发送给模型：

1. 整门课各讲、各部分的标题；
2. 当前讲的大纲；
3. 当前页全文及相邻两页；
4. 按问题关键词在整门课中检索出的相关页。

追问时还会加上本对话之前的若干轮问答（见 `history_budget`）。**历史里存储当初的问题和回答原文，

回答以 SSE 逐段推给界面（`POST /api/chat/stream`），使用本地服务的 HTTP/1.0 连接关闭定界，
不需要 chunked 编码。中转站若无视 `stream: true` 则直接返回整段 JSON，程序会退化成一次性显示。

## 项目结构

```
os-course-reader/
├── app.py            # 入口：启动服务并打开窗口
├── agent.py          # 课程知识库、检索与 LLM 调用
├── history.py        # 对话历史：按讲分文件保存、原子写、增长上限
├── sync.py           # 更新：git pull + 重新渲染
├── pdf_export.py     # 导出 PDF 的独立脚本（由外部 Python 子进程执行）
├── wizard.py         # 环境检测与逐项安装
├── server.py         # 本地 HTTP 服务与 JSON API
├── config.py         # 配置读写与路径解析
├── config.json       # 用户配置
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
| Chrome | 打印 PDF 目录页 |


## 更新日志

### v1.0.0

- **流式回答**：打字机效果，公式实时渲染；
  已经收到的部分会保留，并照常存进历史。
- **对话历史**：对话按讲分开保存，标题由模型自动生成；追问时会带上之前的问答。
- **Markdown 排版**：补上表格、引用块、分隔线、链接、删除线，以及可嵌套的有序 / 无序列表。
  同时修改三处渲染错误 —— 代码块里的空行会把代码块拆开、代码里的 `$变量` 被当成公式、
  代码块的语言标记（` ```bash ` 里的 `bash`）漏进正文；并堵掉一处链接属性注入。

## 注意事项

- 课件渲染本身不导出 PDF，渲染阶段不需要 Chrome；**导出 PDF** 才会用到。
- `git pull` 需要能够访问 GitHub。若网络受限，更新会回退至本地已有版本并在日志中提示，不影响课件浏览。
- 本程序通过本地回环地址提供页面与服务，不对外网开放；仅有 LLM 请求会访问外部 API。
- 向导会真实改动你的系统。它只在你**逐项点击按钮**后执行。
