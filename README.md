# ICS 课件阅读器

面向上海交通大学《计算机系统基础》（ICS / CS:APP）课程的本地课件阅读器，附带一个具备课程上下文理解能力的 AI 助教。它将课程仓库（基于 [lecturekit](https://github.com/SJTU-IPADS/OS-Course-Lab) 编写）中的课件渲染为网页幻灯片，并在原生窗口中提供双栏阅读体验：左侧浏览课件，右侧随时就当前页提问。

## 功能特性

- **原生窗口**：以单文件 `.exe` 运行，无需浏览器，界面简洁。
- **双栏阅读**：左侧复用 lecturekit 自带阅读器（大纲 → 点选页面 → 方向键翻页），右侧为常驻对话侧栏。
- **页面感知**：顶部实时显示当前阅读位置（第 N 页《标题》），提问时自动带上页码。
- **上下文理解**：回答时注入当前页全文、前后页、本讲大纲、整门课结构，以及按问题关键词检索到的相关页，而非仅依赖单页内容。
- **自动更新**：启动时自动执行 `git pull` 并重新渲染课件，也可通过界面按钮手动触发。
- **可配置 API**：采用 OpenAI 兼容接口，`base_url` / `model` / `api_key` 均可编辑，支持 DeepSeek、OpenAI、Moonshot、智谱、本地 Ollama 等。
- **公式渲染**：助教回答中的 LaTeX 公式（行内 `$...$` 与块级 `$$...$$`）由内置 KaTeX 离线渲染，无需联网。

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
| `api_base` | `https://api.deepseek.com` | OpenAI 兼容接口的根地址 |
| `model` | `deepseek-chat` | 模型名称 |
| `api_key` | （空） | API 密钥，仅保存在本地 |
| `python` | `python` | 用于渲染课件的 Python 解释器 |
| `auto_update` | `true` | 启动时是否自动更新 |
| `port` | `8787` | 本地 HTTP 服务端口（占用时自动递增） |
| `max_retrieve` | `4` | 提问时额外检索进上下文的相关页数量 |
| `system_prompt` | （空） | 自定义助教提示词，留空则使用默认值 |

### 常见 API 组合

```jsonc
// DeepSeek（国内直连）
{ "api_base": "https://api.deepseek.com", "model": "deepseek-chat" }

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
├── server.py         # 本地 HTTP 服务与 JSON API
├── config.py         # 配置读写与路径解析
├── config.json       # 用户配置（本地生成，不提交）
├── web/              # 前端界面（HTML / CSS / JS）
├── stubs/            # Windows 兼容占位模块（fcntl / pty / termios）
├── build_exe.ps1     # PyInstaller 打包脚本
└── README.md
```

## 环境依赖

- Python ≥ 3.12，且已在 `ICS-PPT/` 目录执行 `pip install -e .`；
- Node.js 与 vendored Marp CLI（在 `ICS-PPT/` 目录执行一次 `scripts/prepare.sh`）；
- Python 依赖：`pywebview`、`httpx`；打包另需 `pyinstaller`。

## 注意事项

- 课件渲染仅生成网页，不导出 PDF，因此**不需要 Chrome**。
- `git pull` 需要能够访问 GitHub。若网络受限，更新会回退至本地已有版本并在日志中提示，不影响课件浏览。
- 本程序通过本地回环地址提供页面与服务，不对外网开放；仅有 LLM 请求会访问外部 API。
