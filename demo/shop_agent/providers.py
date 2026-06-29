"""LLM provider abstraction for the demo agent.

We unify Claude, OpenAI, and a Mock provider behind one tiny interface:

    LLMResponse = {role: "assistant", text: str | None, tool_calls: list[ToolCall]}

The agent loop is provider-agnostic; provider classes translate to/from the
native SDK shapes.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

SYSTEM_PROMPT = """你是一个电商客服助手。你只能处理订单查询、物流追踪、退换货、发票、支付相关问题。
当前用户已登录，user_id 通过工具自动注入，你不需要询问。

工作流：
1. 如果用户问订单/物流/退货，先调用相应的工具拿到真实数据。
2. 拿到数据后用中文清晰回答，订单/物流号原样写出来。
3. 如果工具返回 error，向用户解释找不到，并请他确认订单号。
4. 不要编造订单号、物流号、价格、状态——只用工具返回的数据。

回答要简洁、口语化、有礼貌。"""


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class LLMProvider:
    """Subclasses translate the common shape to/from a native SDK."""

    name: str = "base"

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        raise NotImplementedError


# ---------------- Claude ----------------

class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        try:
            import anthropic
        except ImportError as e:
            raise ImportError("pip install anthropic to use ClaudeProvider") from e
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model or "claude-haiku-4-5"

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        anthro_messages = []
        for m in messages:
            role = m["role"]
            content = m["content"]
            if role == "tool":
                # Anthropic represents tool results as a user message with
                # a tool_result content block.
                anthro_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m["tool_call_id"],
                        "content": json.dumps(content, ensure_ascii=False),
                    }],
                })
            elif role == "assistant_tool_call":
                # An assistant turn that issued tool calls. Replay the blocks.
                blocks = [{"type": "text", "text": content.get("text") or ""}]
                for tc in content["tool_calls"]:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc["id"], "name": tc["name"], "input": tc["args"],
                    })
                anthro_messages.append({"role": "assistant", "content": blocks})
            else:
                anthro_messages.append({"role": role, "content": content})

        resp = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=tools or [],
            messages=anthro_messages,
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
            elif getattr(block, "type", None) == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id, name=block.name, args=dict(block.input)
                ))

        return LLMResponse(
            text="".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )


# ---------------- OpenAI ----------------

class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError("pip install openai to use OpenAIProvider") from e
        self._client = OpenAI(api_key=api_key) if api_key else OpenAI()
        self.model = model or "gpt-4o-mini"

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        oai_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in messages:
            role = m["role"]
            content = m["content"]
            if role == "tool":
                oai_messages.append({
                    "role": "tool",
                    "tool_call_id": m["tool_call_id"],
                    "content": json.dumps(content, ensure_ascii=False),
                })
            elif role == "assistant_tool_call":
                oai_messages.append({
                    "role": "assistant",
                    "content": content.get("text") or "",
                    "tool_calls": [{
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"], ensure_ascii=False),
                        },
                    } for tc in content["tool_calls"]],
                })
            else:
                oai_messages.append({"role": role, "content": content})

        oai_tools = None
        if tools:
            oai_tools = [{
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            } for t in tools]

        resp = self._client.chat.completions.create(
            model=self.model,
            messages=oai_messages,
            tools=oai_tools,
            max_tokens=1024,
        )
        choice = resp.choices[0]
        msg = choice.message
        tool_calls = []
        for tc in (msg.tool_calls or []):
            tool_calls.append(ToolCall(
                id=tc.id, name=tc.function.name,
                args=json.loads(tc.function.arguments or "{}"),
            ))
        return LLMResponse(
            text=msg.content,
            tool_calls=tool_calls,
            input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            output_tokens=resp.usage.completion_tokens if resp.usage else 0,
        )


# ---------------- Mock (for tests and "no API key" demos) ----------------

class MockProvider(LLMProvider):
    """Deterministic fake LLM. Useful in tests and lets users try the demo
    without an API key — the responses are clearly canned but the guard /
    agent / tool flow is real.
    """

    name = "mock"

    def __init__(self, latency_ms: int = 60):
        self.latency_ms = latency_ms
        self._call_count = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        time.sleep(self.latency_ms / 1000.0)
        self._call_count += 1

        # Find the most recent user message text — that's what we react to.
        user_msg = ""
        for m in reversed(messages):
            if m["role"] == "user" and isinstance(m["content"], str):
                user_msg = m["content"]
                break

        # Last entry is a tool result → time to summarize
        last = messages[-1] if messages else None
        if last and last.get("role") == "tool":
            content = last["content"]
            return self._summarize_tool_result(content)

        # First-pass logic: very small intent rules
        lower = user_msg.lower()

        # Social niceties — greet back warmly, no tool calls.
        GREETINGS = ("hi", "hello", "你好", "您好", "早上好", "下午好", "晚上好",
                     "嗨", "哈喽")
        THANKS = ("谢谢", "thanks", "thank you", "感谢")
        BYE = ("再见", "拜拜", "bye", "goodbye")
        if any(g in lower for g in GREETINGS) and len(user_msg) <= 12:
            return LLMResponse(
                text="您好 😊 我是这里的电商客服小助手。\n您可以问我：\n📦 查订单（比如 \"ORD-1001 到哪了\"）\n🚚 追物流（比如 \"SF-9988-7766 在哪\"）\n↩️ 退换货 / 发票相关问题",
                input_tokens=80, output_tokens=60,
            )
        if any(t in lower for t in THANKS):
            return LLMResponse(
                text="不客气，还有什么可以帮您的吗？",
                input_tokens=80, output_tokens=15,
            )
        if any(b in lower for b in BYE):
            return LLMResponse(
                text="好的，祝您生活愉快～有问题随时回来 👋",
                input_tokens=80, output_tokens=15,
            )
        if "怎么用" in user_msg or "你能做什么" in user_msg or "你能帮我" in user_msg:
            return LLMResponse(
                text="我可以帮您：\n• 查询订单状态、物流位置\n• 申请退货 / 换货 / 退款\n• 发票和支付问题\n\n试试问 \"我的 ORD-1001 到哪了\" 吧 🙂",
                input_tokens=80, output_tokens=60,
            )

        if "ord-" in lower:
            # Extract the order id
            import re
            m = re.search(r"ord-\d+", lower)
            if m:
                return LLMResponse(
                    text=None,
                    tool_calls=[ToolCall(
                        id=f"call_{self._call_count}", name="get_order",
                        args={"order_id": m.group(0).upper()},
                    )],
                    input_tokens=120, output_tokens=20,
                )
        if "sf-" in lower:
            import re
            m = re.search(r"sf-[\d-]+", lower)
            if m:
                return LLMResponse(
                    text=None,
                    tool_calls=[ToolCall(
                        id=f"call_{self._call_count}", name="get_shipment",
                        args={"shipment_id": m.group(0).upper()},
                    )],
                    input_tokens=120, output_tokens=20,
                )
        if any(k in user_msg for k in ("订单", "快递", "物流", "耳机", "包裹")):
            return LLMResponse(
                text=None,
                tool_calls=[ToolCall(
                    id=f"call_{self._call_count}", name="list_orders", args={},
                )],
                input_tokens=120, output_tokens=15,
            )
        if "退" in user_msg:
            return LLMResponse(
                text="好的，请告诉我订单号，我帮您处理 😊",
                input_tokens=120, output_tokens=30,
            )
        return LLMResponse(
            text=(f"嗯，我没太理解您说的 {user_msg!r}。\n"
                  "您可以试试：\n"
                  "• 报订单号（比如 \"ORD-1001\"）\n"
                  "• 报快递号（比如 \"SF-9988-7766\"）\n"
                  "• 或者直接说 \"想退货\" / \"查物流\""),
            input_tokens=100, output_tokens=50,
        )

    def _summarize_tool_result(self, result: Any) -> LLMResponse:
        if isinstance(result, dict) and result.get("error"):
            return LLMResponse(
                text=("嗯～我这边没查到对应记录哎，麻烦您再确认下订单号 / 快递号？"
                      f"\n（系统返回：{result.get('error')}）"),
                input_tokens=200, output_tokens=40,
            )
        # Order check goes first — an order dict can also have shipment_id.
        if isinstance(result, dict) and "order_id" in result:
            items = "、".join(i["name"] for i in result.get("items", []))
            return LLMResponse(
                text=(f"找到啦 ✅\n订单号：{result['order_id']}\n"
                      f"商品：{items}\n"
                      f"金额：¥{result.get('total')}\n"
                      f"状态：{result.get('status')}"),
                input_tokens=280, output_tokens=70,
            )
        if isinstance(result, dict) and "carrier" in result:
            return LLMResponse(
                text=(f"快递 {result['shipment_id']} 状态如下 📦\n"
                      f"承运：{result.get('carrier','?')}\n"
                      f"当前位置：{result.get('current_location','?')}\n"
                      f"状态：{result.get('status')}\n"
                      f"预计 {result.get('estimated_delivery','?')} 送达"),
                input_tokens=300, output_tokens=80,
            )
        if isinstance(result, dict) and "orders" in result:
            n = len(result["orders"])
            ids = "、".join(o["order_id"] for o in result["orders"])
            return LLMResponse(
                text=f"您目前有 {n} 个订单：{ids}\n想查哪一个呢？",
                input_tokens=300, output_tokens=60,
            )
        return LLMResponse(
            text=f"工具返回了一些数据：{str(result)[:200]}",
            input_tokens=200, output_tokens=50,
        )


# ---------------- factory ----------------

def make_provider() -> LLMProvider:
    """Pick a provider based on env vars.

    LLM_PROVIDER=mock           → MockProvider (default if no key)
    LLM_PROVIDER=claude         → ClaudeProvider (needs ANTHROPIC_API_KEY)
    LLM_PROVIDER=openai         → OpenAIProvider (needs OPENAI_API_KEY)
    LLM_PROVIDER unset → use whichever key is present; if neither, mock.
    """
    explicit = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if explicit == "mock":
        return MockProvider()
    if explicit == "claude":
        return ClaudeProvider(model=os.environ.get("LLM_MODEL"))
    if explicit == "openai":
        return OpenAIProvider(model=os.environ.get("LLM_MODEL"))

    if os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeProvider(model=os.environ.get("LLM_MODEL"))
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIProvider(model=os.environ.get("LLM_MODEL"))
    return MockProvider()
