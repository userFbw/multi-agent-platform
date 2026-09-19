# AI 原生全栈应用开发平台

题目 29 的课程设计。用户在页面上写一句中文需求，平台依次叫起几个 AI Agent：先出 PRD，再拆前后端任务书，然后写前后端代码、跑测试，最后给出一个能直接跑起来的项目。整套流程可以看到每一步是谁做的、产出了什么。

## 主要功能

- 一句需求生成应用，纯前端或前后端分离都支持
- 两种编排方式：一种是自己选模板或在画布上连线（`workflow`），一种是让任务编排官读 PRD 现场排图（`agent`）
- PRD 出来以后停下来等用户确认，确认完才继续开发，PM 不会重复跑
- QA 报告判不通过就不交付：项目标失败并写明原因，不打包成一个"看起来成功"的结果
- 前后端分离项目跑完会自动启动一次，页面上能直接打开看，也能下载 ZIP
- 不满意可以改：增量修改（在上一版代码上改，附一份本轮改动清单）或重新生成（清空重写）
- 跑到一半想停可以点「终止运行」，项目标成已终止，已经产出的东西保留
- 可以自己造 Agent：写一句描述生成提示词，看过没问题再存下来，就成了画布上能拖的节点
- 每个 Agent 的运行日志、每次真跑的报错原文、每一步的会话号和耗时都能查到

## 用到的技术

前端 Vue 3 + TypeScript + Vite，状态用 Pinia，编排画布用 @vue-flow。
后端 Python 3.14 + FastAPI + SQLAlchemy 2 + Pydantic v2，数据库 SQLite。
AI 这一层不自己管模型，调 DeepSeek Harness 的命令行（`dsh --profile headless`），提示词放在 `.dsh/skills/` 下的 12 个技能里。
Agent 执行命令时跑在 bubblewrap 沙箱里，生成的项目各自建一个虚拟环境。

服务器上实测版本：Python 3.14.4、Node v22.23.2、dsh 0.1.2-rc.1、bubblewrap 0.11.1。
后端依赖见 `requirements.txt`。

## 怎么跑起来

服务器上要有这些：Python 3.14（仓库里带了 `new_venv/`）、Node 22（`frontend/node_modules/` 已经装好）、`dsh` 命令行并且登录过、`bubblewrap`。8000（后端）、3000（前端）、8100（生成的应用）三个端口要空着。

启动后端（不要加 `--reload`）：

```bash
cd "/opt/multi agent/backend" && "/opt/multi agent/new_venv/bin/python" -m uvicorn main:app --host 0.0.0.0 --port 8000
```

启动前端：

```bash
cd "/opt/multi agent/frontend" && npm run dev -- --host 0.0.0.0 --port 3000
```

然后浏览器打开 `http://服务器IP:3000`。前端的 `/api`、`/previews`、`/avatars` 会转发到后端去。

数据库如果被删了或者想重建（重复执行也没关系）：

```bash
cd "/opt/multi agent/backend" && PYTHONPATH=. "/opt/multi agent/new_venv/bin/python" app/db/init_db.py
```

## 演示账号

| 账号 | 密码 | 说明 |
|---|---|---|
| `cwf` | `123456` | 早期测试账号 |
| `fbw` | `123456` | 10 个项目，有几个前后端分离的 |
| `fbw112233` | `111` | 2 个项目（五子棋、计时器） |

密码是明文存在 `backend/data/project.db` 里的，课程设计没做加密，正式用要改。

## 使用流程

1. 注册或登录，进项目列表
2. 新建项目，写一句需求（比如"做个五子棋小游戏"），选编排方式：`workflow` 模式选内置模板或自己的图，`agent` 模式不用选，交给编排官
3. 等 PRD 出来，项目会停在"待审批"
4. 看过 PRD 后点通过（有补充要求可以填在里面）；不满意也可以驳回，驳回会把意见并进需求重跑 PRD
5. 平台自动往下跑：架构师拆任务书 → 前后端开发 → QA 出测试报告 → 通过就打包并启动应用
6. 在项目页右侧看各 Agent 的产出、运行日志和项目 BUG，在"预览"里打开应用，或者下载 ZIP；要改就点增量修改或重新生成

## 目录说明

```
/opt/multi agent/
├── backend/                    后端
│   ├── app/api/                对前端的接口
│   ├── app/agents/             Agent 调度（编排、图执行、调 DSH）
│   ├── app/db/                 数据库
│   ├── app/storage/            文件和打包
│   ├── app/runner/             生成应用的启停、虚拟环境
│   ├── app/core/               配置、token、启动守卫
│   ├── main.py                 入口
│   ├── .env                    配置（对外主机名、超时阈值）
│   ├── data/                   平台自己的数据：project.db、头像、会话日志
│   └── exports/u<用户>/p<项目>/ 每个生成的项目：源码、PRD、运行日志、数据、ZIP
├── frontend/                   前端（Vue 3 + TS + Vite）
│   └── src/{api,stores,views,components,utils}
├── .dsh/skills/                12 个技能的提示词，Agent 全靠它
├── new_venv/                   后端运行环境，也是 Agent 沙箱里的 python3
├── requirements.txt            后端依赖
└── Readme.md、项目架构.md、项目实施方案.md
```

## 几条注意事项

- 后端**不要**用 `--reload` 启动。开了以后监听 8000 的是父进程，它重启子进程的那几秒没人接请求，页面就一直转圈；而且平台每生成一个项目都会往 `backend/exports/` 下写几百个 `.py` 文件，会不停触发重载，正在跑的任务会被打断。平台加了守卫：启动时检测到 reload 会打横幅，新建/审批/改代码/重跑这四个入口直接拒绝。
- `new_venv/` 不要删。Agent 在沙箱里执行的 `python3` 就是它（靠 PATH 找到的），删了以后所有生成项目的自检和真跑都用不了。
- `.dsh/skills/` 不要删，Agent 的提示词都在里面。
- `backend/data/project.db` 不要删，演示用的账号和项目都在里面。
- 8100 是生成应用的固定端口，平台自己不要占用。

## 文档

| 文件 | 内容 |
|---|---|
| `Readme.md` | 项目介绍、启动方式、使用流程 |
| `项目架构.md` | 系统架构、实现方法、技术栈、数据表、接口 |
| `requirements.txt` | 后端依赖，与 `new_venv` 一致 |
| `项目实施方案.md` | 题目需求和总体方案 |

## 目前没做的

- 登录只做到"能识别是谁"，没做完整的权限校验，密码也没加密
- 单机单实例部署，SQLite 单文件，没有容器化和 CI，隔离靠 bubblewrap 加项目自己的虚拟环境
- 生成的代码质量取决于模型，平台只保证流程走通和"确实真跑过"
- 也没给会话加强制超时，卡住了靠用户在页面上点终止
