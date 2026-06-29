# Demo · 电商客服 agent

一个开箱即用的小 demo，让你**直观看到 domain-guard 在 LLM agent 里到底拦在哪一步、省了多少**。

```
用户消息
   │
   ▼
┌─────────────────┐
│  domain-guard   │ ← 这里是关键。off-topic 在这里被拦截，零 LLM 调用。
└────────┬────────┘
         │ pass
         ▼
┌─────────────────┐
│   LLM #1        │ 解析意图、决定要不要调工具
└────────┬────────┘
         ▼
┌─────────────────┐
│   tool          │ 查 mock 订单 / 物流数据
└────────┬────────┘
         ▼
┌─────────────────┐
│   LLM #2        │ 用工具结果生成回复
└────────┬────────┘
         ▼
      assistant reply
```

UI 把上面每一步都画成彩色时间线，让你看到 guard 在哪个环节、用了多少时间、消耗了多少 tokens。

## 30 秒上手

```bash
cd demo
cp .env.example .env
# 编辑 .env，填入 ANTHROPIC_API_KEY（推荐）或 OPENAI_API_KEY
# 都不填也行 — 会用 mock 模式（无需 API key，无网络调用）

./run.sh
```

浏览器自动打开 `http://localhost:9000`。

## 你能看到什么

### 1. 守卫拦截的可视化

发送 "你是什么模型啊" → 你会看到：
- 红色 `guard_blocked` chip，标注 `rule · 0.95` 置信度
- 一个绿色横幅："✓ guard 提前拦截 — 节省约 720 tokens（避免了 LLM 调用）"
- 顶部计数器 **被拦截 +1**、**节省 tokens +720**

发送 "我的 ORD-1001 订单到哪了" → 你会看到：
- 紫色 `guard` 放行（耗时 < 1ms）
- 蓝色 `llm` 调用 #1（agent 调 `get_order` 工具）
- 绿色 `tool` 调用（查 mock 数据）
- 蓝色 `llm` 调用 #2（生成回复）
- 实际 token 消耗

### 2. "有/无 guard" 对比

顶部的 **guard 启用** 开关。关掉后，跑题消息也会进 LLM——你会看到 token 数蹭蹭涨。

### 3. 一键加载预设对话

右侧的"试试看"卡片：8 种典型场景，绿条 = 应该放行，红条 = 应该被拦。点一下自动发送。

### 4. 多轮对话与上下文豁免

"多轮对话"那个脚本演示了 guard 的 `context_bypass` 层：第二轮用户只发了 `ORD-1002`（这种短消息单独看会被 embedding 拦），但因为前一轮已经在收集订单号的流程里，guard 放行了它。

## 文件布局

```
demo/
├── .env.example             配置模板
├── run.sh                   一键启动
├── README.md                就是这个文件
├── data/
│   ├── orders.json          4 个 mock 订单（属于 alice / bob）
│   ├── shipments.json       3 条快递记录
│   └── conversations.json   8 个推荐对话脚本
├── guards/
│   └── shop-support.yaml    电商客服 guard 配置（你可以改这个文件实验）
├── shop_agent/
│   ├── server.py            FastAPI 服务 (/api/chat, /api/stats)
│   ├── agent.py             agent 主循环（guard → LLM → tool → LLM）
│   ├── providers.py         Claude / OpenAI / Mock 三种 LLM provider
│   ├── tools.py             get_order / list_orders / get_shipment / submit_return
│   ├── tracker.py           每条消息的轨迹结构
│   └── ui.html              单文件 SPA
└── tests/
    └── test_demo.py         端到端测试（用 Mock provider，零烧钱）
```

## 自定义实验

### 改一条 guard 规则

编辑 `demo/guards/shop-support.yaml`，加一条新的 `block_patterns`。重启服务后立即生效。

或者打开 `http://localhost:9000/admin`（domain-guard 自带的 admin UI），直接在浏览器改 YAML。

### 加一个新工具

`demo/shop_agent/tools.py` 里加一个函数，再在 `TOOL_SPECS` 里描述它。无需改 agent 主循环——它会被自动调起。

### 切到 OpenAI

`.env` 里：
```
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
```

### 完全离线（无 API key）

`.env` 里把两个 key 都留空。会用 `MockProvider`——固定逻辑的假 LLM，能完成订单/物流查询，response 前面会带 `[mock]` 前缀让你知道这是假的。这种模式下 token 计数仍然按 mock provider 报告的值累加，体感正确。

## 端到端测试

```bash
cd demo
pytest tests/
```

测试用 mock provider，不烧 token，覆盖：
- guard 拦截的消息**确实没进 LLM**
- 放行的消息走完 guard → LLM → tool → LLM 全链路
- 工具调用拿到正确的 mock 数据
- 多轮对话中 context_bypass 生效

## 把这套用到你自己的 agent

这个 demo 的核心其实就 ~80 行（`shop_agent/agent.py` 的 `chat()` 方法）。三件事：

1. **每次接到用户消息，先 `guard.check(message, ctx)`**——`pass` 才继续。
2. 把守卫的 `result.fallback_reply` 作为拒答模板。
3. agent 的 LLM 主循环不需要任何改动——guard 在它之前。

剩下都是上层逻辑：tool calling、状态管理、token 计费——这些你的 agent 已经在做。

## 常见问题

**Q: 一定要有 GPU / 大模型才能用 guard 吗？**
不需要。这个 demo 的 guard 默认只跑 rule + embedding 两层，跑题消息在 rule 层就被拦了，CPU 微秒级。LLM 只在你**确实让 agent 干活**的时候才会被调。

**Q: 我的 LLM 是开源的（如 Llama / Qwen）能用吗？**
能。你只需要在 `providers.py` 里照着 `ClaudeProvider` 写一个 `LlamaProvider`，实现 `chat(messages, tools)` 返回 `LLMResponse` 即可。tool calling 协议大同小异。

**Q: guard 自己会不会拒错？**
会。这就是为什么有 `mode: shadow` 和 `guard-cli replay` 工具——拿真实日志先评估。详见上层 `docs/user-guide.md`。
