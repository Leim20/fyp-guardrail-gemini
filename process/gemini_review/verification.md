# 对 Gemini 架构评审的核实（2026-11-28）

`guardrail_architecture_review.md` 是 Gemini 基于 2026-11-25 20:28 的快照写的评审。这份文件对照当前代码、
文档和 IR，逐条核实它的说法。评审读取快照 30 分钟后，guard 改用了 iteration 3 classifier（见
`GUARDRAIL_CHANGES.md` 的 C1），所以评审里有几个数字已经过时。

## 1. 评审称"尚未被发现"的问题

> **状态（2026-11-28）：** 下表中核实为属实的项目，已在 R1（commit `d1ed3f6`）修复。细节见 `GUARDRAIL_CHANGES.md` 的 R1 一节。被 BLOCK 的用户消息仍会写进 ledger，这一项按原来的理由不改。

| 评审的说法 | 核实 | 判断 |
|---|---|---|
| `create_ticket` 四层全穿 | 这是刻意的范围决定：`domains/novamart/tools.yaml` 中 create_ticket 的 rationale、威胁模型 §7。工单只进内部队列，由人工处理。 | 不是新发现。可以低成本加固：给 create_ticket 加 `allowed_content`。 |
| 被 BLOCK 的用户消息仍写入 ledger | 已列在 `GUARDRAIL_CHANGES.md` "尚未处理" → "已知但不在计划内"。用户本人就是 principal，policy 仍然约束他能做的事。 | 不是新发现，影响小。 |
| `record_outcome` 用 `.get` 而不是 `.pop` | 属实。进程内调用时，同一个调用被放行过一次，之后重复执行不会被标记。HTTP 服务对 ASK_CONFIRM 有一次性 token，不受影响。 | 真问题，小。 |
| fail-closed 审计记录缺 session 字段（`api/main.py`） | 属实。 | 真问题，很小。 |
| 把详细的 BLOCK 原因返回给 LLM（`agent/loop.py`） | 属实。这是示例 agent 的选择，不在 guard 引擎里。 | 合理，可以改成粗粒度错误码。 |
| head+tail 截断导致长文档中间有盲区 | 只对一半。检索到的内容会逐句打分（`guardrail/detector.py`），中间的句子也会被检查。只有一整段超过 512 token、又没有句子分隔的文本才会漏掉中间部分。 | 边缘情况，可以对长句分块。 |

## 2. 过时的数字（快照之后已经改变）

- **"Iteration 3 没能上线"**：现在已经上线，作为一次有记录的偏离（`docs/distillation_plan.md` §10）。
- **"DeBERTa 对隐蔽注入打 0.00003，对纠错句打 0.99995"**：这是 iteration 2 的数字。换成 iteration 3 后：
  - B01（纠错句）在 detectors 变体下不再被误拒；
  - 合成测试集 A 类（数据里的间接注入）的识别率从 21% 升到 97.5%。
- **"no_judge 下仍有 6 个攻击只靠确认"**：现在是 0/18。但结构性论点仍然成立："policy 允许、但用户没要求"的动作，在确定性层里没有专门的防线；现在能挡住，是因为 classifier 认出了这些具体写法。

## 3. 核实为属实的重要批评

- **没有 guard 时，DeepSeek 端到端的攻击成功率也是 0/10**（`docs/results_p4_core.md`）。端到端对比显示不出 guard 的价值。forced compromise 从方法论上处理了这个问题，但 E11 还需要一个容易被攻破的 agent（例如本地 Ollama 7–8B）。
- **只有完整性方向的 taint，没有机密性方向。** 通用引擎不检查"敏感值流向对外工具"。NovaMart 部分有覆盖：回复会检查受保护的值，`send_email` 的收件人固定。
- **IR Figure 3.30 的数字属实**：代码或命令执行 93.8%（30/32），访问个人记录 71.9%，外部 API 和修改数据各 65.6%。
- **Agent 和 judge 都用 DeepSeek**：同一个模型家族可能会被同一类话术一起骗过。

## 4. 评审里的架构建议

- **MCP Security Proxy。** MCP 层只看得到工具调用和工具结果，看不到用户的消息，也看不到最后给用户的回复。放在这一层，guard 会失去：
  - 用户输入检查；
  - 输出检查；
  - alignment judge 需要的用户原话；
  - "用户自己打的值不算 taint"的豁免。

  所以它不是"零侵入就能用完整的 guard"，而是一个功能缩水的强制执行层。适合作为 future work。
- **Turn Capability Compiler。** 本轮的权限范围仍然要由 LLM 或规则从用户原话推断出来，判断的问题和 alignment judge 相同，只是时间提前了。真正的好处是：推断发生在读取任何不可信内容之前，攻击者完全无法影响它。和 CaMeL、Progent 的比较适合放进 discussion 或 future work。
- **DevOps 领域里的 `run_shell_command`。** guard 无法判断一条 shell 命令的内容是否安全，最多只能把它设成每次都要确认（ASK_CONFIRM）。可以作为一个工具加进第二个领域，但不应该让整个领域围绕它来设计。

## 5. 截止日前的计划（2026-12-16）

1. 修掉第 1 节里核实为属实的小问题。
2. 第二个领域包：Workspace 助手。
   - 工具：读邮件、读敏感文件、发邮件给任意收件人、webhook，外加一个需要确认的命令工具。
   - 新增一个原语，检查机密值是否出现在对外工具的参数里。
   - 引擎核心不改。
3. 用本地弱模型（Ollama）作为容易被攻破的 agent，做端到端对比。
4. E11 held-out 集覆盖两个领域，再加 E12 prompt-only 对照组。
5. 冻结 guard，然后 E11、LLMail blind test、端到端评估各只跑一次。
