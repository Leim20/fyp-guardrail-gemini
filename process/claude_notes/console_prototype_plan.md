# Guardrail Console：界面设计，以及第一步（高保真原型）的实现计划

## Context

- Guardrail 是 middleware，本身没有界面。Marker 和 supervisor 需要看到它在工作，所以要一个界面；这个界面是 **guardrail 的检查台**，不是 agent 的聊天界面。IR §1.5.1 要求显示 input、layer-level findings、verdict、reason、proposed tool call、latency，目标用户是开发者。
- 你的要求：炫酷、动画丝滑、有实时动态、能从中拿到有用的内容。
- 你的三个决定：**深色和浅色两套主题同等打磨**；主画面是**实时管线**；**现在只做原型**（约 1 天），你确认外观后先回主线（FastAPI → 第二个领域 → held-out 评估），最后再接真实数据、补其余视图。
- 现状：只有 `agent/cli.py` 的终端输出；`api/` 是空的；机器没有 Node，所以前端不能用需要构建的框架。
- IR 把 dashboard 定为 FR08（Could）。它是展示层，不是研究贡献，所以这一步限定在 1 天。

## 设计

### 核心想法
把 guardrail 画成一条实时流动的管线。每一次检查是一个光点，依次穿过各层 "关卡"，被哪一层拦下就停在哪一层。不用读日志就能看出 "哪一层、因为什么、拦下了什么"。

### 主画面（Live）

```
┌ Guardrail Console ─ domain: novamart ─ guard: full ▾ ───────────── ● live ─ ☾/☀ ┐
│ checks 128   blocked 17   sanitized 9   confirm 6   judge calls 11   p50 28 ms  │
├─────────────────────────────────────────────────────────────────────────────────┤
│  sources          INPUT                     AGENT       ACTION                  │
│  ○ user      ─▶ [trust][rules][clf][judge] ─▶ ◎ LLM ─▶ [schema][policy]         │
│  ○ internal  ─▶                                        [taint][alignment] ─▶ ⚙  │
│  ○ untrusted ─▶         ●→ →   ✖ 停在 taint                                      │
│  ☺ user ◀──────── [leak][links][pii] ◀─────────── reply                         │
├────────────────────────────────┬────────────────────────────────────────────────┤
│ Live decisions                 │ 选中的这一次检查                                │
│ 15:35:06 action BLOCK taint    │ 每一层：结果 · 分数 · 耗时                      │
│ 15:35:04 input  SANITIZE clf   │ 原文：被删的句子划掉；taint 的值高亮并连线      │
│ …（新行滑入）                  │ judge 的理由 · 审计记录 JSON · 复制             │
├────────────────────────────────┴────────────────────────────────────────────────┤
│ ▶ 场景 [DIR-001] [IND-006] [A16] …        速度 0.5× 1× 2×        ⏸ 暂停环境流量 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 每个区域给出什么有用的内容

| 区域 | 回答的问题 |
|---|---|
| 顶部数字条 | 到现在为止检查了多少、各种 verdict 各多少、judge 被调用了多少次、延迟多大 |
| 管线 | 这一次检查走了哪些层，哪一层做的决定。每个关卡上有计数，累计起来就是 "各层拦截占比" |
| 决定列表 | 最近发生了什么；鼠标悬停在某个关卡上，列表只显示由这一层决定的记录 |
| 详情面板 | 为什么这样判：逐层结果、分数、被删掉的句子、taint 的值来自哪里、judge 的理由、原始审计记录 |
| 场景条 | 让一个指定的攻击或正常请求立刻走一遍，用于讲解 |

可以带走的内容（写报告用）：详情面板一键复制审计记录 JSON；管线和数字条在浅色主题下可直接截图。图表导出（SVG/CSV）放在后面的 Evaluation 视图里，不在这一步。

### 动画（每一个都对应一个含义）

| 动画 | 含义 |
|---|---|
| 光点沿管线移动 | 一次检查正在进行 |
| 关卡在评估时亮起：通过后变暗，标记时变琥珀色，拦截时变红并扩散一圈 | 这一层的结果 |
| BLOCK：光点停在做决定的关卡并消散。SANITIZE：光点变小、变琥珀色后继续。ASK_CONFIRM：光点在出口停住，外面一个等待的圆环 | 四种 verdict 看起来不一样 |
| 新的决定从上方滑入列表；数字滚动到新值 | 实时 |
| 点一行，详情面板的内容交叉淡入，逐层结果依次出现 | 从概览到细节 |
| 一条线从文档里的值画到 tool 参数 | Taint：这个值从哪里来 |
| 切换主题时整页交叉淡化 | 两套主题是同一个界面 |

规则：只改 `transform` 和 `opacity`，保证 60fps；时长 150–400 ms；页面不可见时暂停；系统开启 "减少动态效果" 时，光点直接跳到下一关，数字不滚动。Verdict 除了颜色还用形状区分，不只靠颜色。

### 两套主题

| | 深色（控制室） | 浅色（开发者工具） |
|---|---|---|
| 背景 | 深蓝黑，细网格 | 白，无网格 |
| 关卡 | 发光描边 | 细线、圆角 |
| 光点 | 带拖尾和光晕 | 实心小圆点 |
| 用途 | 现场演示 | 报告截图 |

实现方式：所有颜色、光晕、线宽都是 CSS 变量，`data-theme` 切换两组值；Canvas 画光点时读同一组变量。四种 verdict 的颜色在两套主题里分别调到足够的对比度。

### 实时数据从哪里来（这一步）

原型阶段没有后端。浏览器加载一份**由真实 guard 跑出来的录制数据**，按真实的先后顺序和节奏播放，并循环播放作为 "环境流量"。点场景条可以插入一个指定的场景。

```
这一步：  console/data/demo_events.json ──▶ stream.js（模拟推送）──▶ 界面
以后：    guard ──▶ FastAPI GET /events（Server-Sent Events）──▶ stream.js ──▶ 界面
```

界面只和 `stream.js` 打交道，所以以后换成真实推送时，其余代码不用改。SSE（Server-Sent Events）是服务器向浏览器单向推送的标准方式，比 WebSocket 简单，断线会自动重连。

### 事件格式（现在定下来，以后 SSE 用同一个）

```json
{"id": 17, "run": "IND-006", "variant": "full", "checkpoint": "action",
 "tool": "update_account", "args": {"field": "phone", "value": "+60 11-6666 6666"},
 "verdict": "BLOCK", "layer": "taint", "reasons": ["tainted_argument: value copied from search_kb:forum_sms_tracking.md"],
 "latency_ms": 0.8,
 "trace": [{"layer": "schema", "status": "pass"}, {"layer": "policy", "status": "pass"},
           {"layer": "taint", "status": "block", "detail": "value copied from …"},
           {"layer": "alignment", "status": "skip"}],
 "text": null, "result_text": null, "removed": [], "scores": {}, "judge": null,
 "taint": {"arg": "value", "value": "+60 11-6666 6666", "source": "search_kb:forum_sms_tracking.md"}}
```

Input 和 output 的事件带 `text`（原文）和 `result_text`（guard 放行的版本）。

## 第一步要做的事（本次批准的范围）

### 0. 先提交 E5–E10
E5–E10 的改动还没有 commit。原型开始前先提交（一个 commit，不带 co-author 行，不 push），让原型从干净的工作区开始。

### 1. 导出录制数据：`scripts/export_console_demo.py`（新文件）
- 用一个包装 guard 记录每一次检查：它的 session 把调用转给真实的 `HybridSession`，同时记下输入的原文、tool 和参数、返回的 `Decision`，以及 `guard.audit.records` 里对应的那条记录。
- 流量来源直接复用现有的回放函数，它们本来就接受任何实现 `open_session` 的 guard：
  - `evaluation/forced_compromise.py` 的 `replay()`：16 个场景。
  - `evaluation/review_baseline.py` 的 `replay()`：37 个 probe。
- 对 5 个变体各跑一遍（`none`、`detectors`、`policy`、`no_judge`、`full`，来自 `guardrail/guard.py` 的 `VARIANTS`），界面上的 "guard" 下拉框就在这些真实结果之间切换。
- `trace` 在这一步由导出脚本从审计记录现有的字段推出来（`scores`、`reasons`、`layer`、`judge`、`removed`），不改引擎。Policy 在管线上是一个关卡，被违反的规则名写在 `detail` 里。
- 原文只写进这份 demo 数据。数据全是合成的；审计日志本身仍然只存 hash 和脱敏预览。
- Judge 的调用走已有的录音（cache 模式），输入和之前的评估相同，不会产生新的 API 调用。
- 输出 `console/data/demo_events.json`。

### 2. 原型：`console/`（新目录，纯静态，无构建、无外部依赖）

| 文件 | 内容 |
|---|---|
| `console/index.html` | 页面骨架 |
| `console/css/tokens.css` | 两套主题的变量：颜色、光晕、线宽、时长、缓动曲线 |
| `console/css/app.css` | 布局和组件样式 |
| `console/js/main.js` | 启动、把各部分接起来 |
| `console/js/store.js` | 很小的状态容器：事件列表、计数、选中项、主题、变体；状态变了通知订阅者 |
| `console/js/stream.js` | 事件来源。现在读 demo 数据并按节奏播放；以后换成 `EventSource('/events')` |
| `console/js/pipeline.js` | 管线：SVG 画节点、关卡和连线，Canvas 叠在上面画光点；位置从 SVG 元素读取，窗口变化时重算 |
| `console/js/feed.js` | 决定列表：滑入动画、选中、按关卡筛选 |
| `console/js/detail.js` | 详情面板：逐层结果、原文对比、taint 连线、JSON、复制 |
| `console/js/kpi.js` | 顶部数字条：数字滚动、延迟的迷你折线 |
| `console/js/theme.js` | 主题切换（View Transitions API，不支持时直接切换） |

技术选择和理由：
- **原生 JavaScript（ES modules）**，不用 React 一类的框架：没有 Node 就没有构建工具；代码量小，每一行你都能在答辩时解释。
- **SVG + Canvas**：SVG 画结构（清晰、能用 CSS 变量换主题），Canvas 画大量移动的光点（不卡）。
- **Web Animations API 和 CSS transition**：浏览器自带，不需要动画库。
- **不从网络加载任何东西**（字体用系统字体）：答辩现场断网也能跑。
- 写迷你折线和数字条之前先读 `dataviz` skill，按它的规则选图形和配色。

### 3. 预览配置：`.claude/launch.json`（新文件）
用 `uv run python -m http.server 8765 --directory console` 启动一个静态服务器，供内置浏览器打开。

### 4. 设计说明：`docs/console_design.md`（新文件）
一页：每个区域回答什么问题、动画和含义的对应表、事件格式、两套主题的取舍。报告的设计章节可以直接用。

## 这一步不做（留到回主线之后）

| 内容 | 需要什么 |
|---|---|
| 真实的实时推送 | FastAPI + SSE（即 FR07 / E19） |
| 任意组合的层开关 | 后端按 `GuardConfig` 重新跑 guard |
| 引擎直接输出逐层 `trace`、demo 模式下带原文的事件 | 改 `guardrail/guard.py`、`guardrail/policy.py` |
| Inspector、Replay、Policy、Evaluation 四个视图，图表导出 | 上面的后端 |

原型的导航栏只放已经能用的 Live，不放点了没反应的按钮。

## 验证

1. `uv run pytest`：169 个测试不变（这一步不改 `guardrail/`、`agent/`、`evaluation/`）。
2. 运行导出脚本，检查：事件数量和回放的检查次数一致；`full` 变体下每个场景的 verdict 和 `results/forced_compromise.json`、`results/review_baseline.json` 里的一致；`data/recordings/judge/` 没有新增文件。
3. 用内置浏览器打开原型：
   - 深色、浅色各截一张图，逐个区域检查对比度和排版。
   - 点场景 IND-006：光点应停在 taint 关卡，列表出现一条 BLOCK，详情里有从帖子到参数的连线。
   - 点一个 SANITIZE 的场景：详情里被删的句子被划掉。
   - 把 "guard" 切到 `none`：同一个场景的光点一路通过。
   - 浏览器控制台没有报错；窗口缩窄后布局不乱。
   - 打开系统的 "减少动态效果"，确认动画降级。
4. 停下来给你看。你提修改意见，改到满意为止，然后回主线。

## 风险

- **时间**：这一步限定 1 天。两套主题同等打磨会占掉其中不少，所以这一步只做主画面。
- **"炫酷" 没有终点**：以上面那张动画对应表为准，不在表里的效果不加。
- **原型的 trace 是导出脚本推出来的**，不是引擎直接给的。它来自真实的审计记录，结果是对的，但 policy 里每条规则各自的通过情况现在看不到。接后端时由引擎直接输出。
