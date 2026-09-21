"""The agent: answer from cited sources and tools, or hand over to a human."""

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session

from app.agent import policy
from app.agent.llm import LLMClient, LLMError, Message
from app.agent.prompts import (
    CLARIFY_TEXT,
    ERROR_TEXT,
    HANDOFF_TEXT,
    SENTIMENT_PROMPT,
    SYSTEM_PROMPT,
    format_sources,
)
from app.agent.tools import TOOLS, run_tool
from app.config import Settings, get_settings
from app.handoff.models import Channel, EscalationReason, Mode, Role
from app.handoff.service import (
    conversation_mode,
    create_ticket,
    get_or_create_conversation,
    record_message,
)
from app.knowledge_base.search import Hit, Searcher

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
CITATION = re.compile(r"\[S(\d+)\]")

ReplyKind = Literal["answer", "clarify", "escalated", "silent"]


@dataclass(frozen=True)
class AgentReply:
    kind: ReplyKind
    text: str = ""
    sources: tuple[str, ...] = ()
    escalation_reason: EscalationReason | None = None


@dataclass
class Agent:
    session: Session
    kb: Searcher
    llm: LLMClient
    settings: Settings = field(default_factory=get_settings)

    async def handle_message(
        self,
        conversation_id: str,
        channel: Channel,
        text: str,
        customer_handle: str = "",
    ) -> AgentReply:
        get_or_create_conversation(self.session, conversation_id, channel, customer_handle)
        record_message(self.session, conversation_id, Role.customer, text)

        # 1. A human owns this conversation — stay quiet.
        if conversation_mode(self.session, conversation_id) is not Mode.bot:
            return AgentReply("silent")

        # 2. Deterministic pre-checks.
        if policy.wants_human(text):
            return self._escalate(
                conversation_id,
                channel,
                EscalationReason.customer_requested,
                policy.summarise(text),
            )
        if policy.wants_restricted_action(text):
            return self._escalate(
                conversation_id, channel, EscalationReason.restricted_action, policy.summarise(text)
            )

        # 3. Frustration (two negative customer messages in a row).
        if await self._is_angry_streak(conversation_id, text):
            return self._escalate(
                conversation_id,
                channel,
                EscalationReason.negative_sentiment,
                f"Customer is frustrated. Last message: {policy.summarise(text, 150)}",
            )

        # 4. Retrieval gate.
        hits = self.kb.search(text, k=5)
        order_question = policy.is_order_question(text)
        best = hits[0].score if hits else 0.0
        if not order_question and best < self.settings.kb_min_score:
            return self._miss(conversation_id, channel, text)

        markers = [f"S{i + 1}" for i in range(len(hits))]
        sources_block = format_sources([(m, h.text) for m, h in zip(markers, hits, strict=True)])
        messages = self._history(conversation_id)
        messages.append({"role": "user", "content": f"{sources_block}\n\nCustomer: {text}"})

        # 5. Tool-use loop.
        used_grounded_tool = False
        for _ in range(MAX_TOOL_ROUNDS):
            try:
                response = await self.llm.complete(
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    tools=TOOLS,
                    max_tokens=self.settings.reply_max_tokens,
                )
            except LLMError as exc:
                log.warning("LLM failure in conversation %s: %s", conversation_id, exc)
                return self._escalate(
                    conversation_id,
                    channel,
                    EscalationReason.low_confidence,
                    f"Model error ({exc}); customer's last message: {policy.summarise(text, 150)}",
                    text=ERROR_TEXT,
                )

            if not response.wants_tools:
                return self._finish(
                    conversation_id, channel, text, response.text, hits, markers, used_grounded_tool
                )

            messages.append({"role": "assistant", "content": response.assistant_content()})
            results = []
            for call in response.tool_calls:
                result = run_tool(self.session, conversation_id, call)
                if result.escalate is not None:
                    return self._escalate(
                        conversation_id,
                        channel,
                        result.escalate,
                        result.summary or policy.summarise(text),
                    )
                used_grounded_tool = used_grounded_tool or result.grounded
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": result.content,
                    }
                )
            messages.append({"role": "user", "content": results})

        return self._escalate(
            conversation_id,
            channel,
            EscalationReason.low_confidence,
            "The assistant could not finish within the tool-call limit.",
        )

    # ---------- helpers ----------

    def _history(self, conversation_id: str) -> list[Message]:
        """Previous turns (excluding the message being handled) in Anthropic format."""
        conv = get_or_create_conversation(self.session, conversation_id, Channel.webchat, "")
        turns = conv.messages[:-1][-self.settings.max_turns_context :]
        messages: list[Message] = []
        for m in turns:
            role = "user" if m.role is Role.customer else "assistant"
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] = f"{messages[-1]['content']}\n{m.text}"
            else:
                messages.append({"role": role, "content": m.text})
        while messages and messages[0]["role"] != "user":
            messages.pop(0)  # a conversation must start with a user turn
        return messages

    async def _is_angry_streak(self, conversation_id: str, text: str) -> bool:
        """Two frustrated customer messages in a row.

        A regex pre-filter keeps this to roughly one extra model call per angry conversation
        instead of one per message.
        """
        if not policy.maybe_frustrated(text):
            return False
        conv = get_or_create_conversation(self.session, conversation_id, Channel.webchat, "")
        previous = [m for m in conv.messages[:-1] if m.role is Role.customer]
        if not previous:
            return False
        if not await self._is_negative(text):
            return False
        return await self._is_negative(previous[-1].text)

    async def _is_negative(self, text: str) -> bool:
        try:
            response = await self.llm.complete(
                system=SENTIMENT_PROMPT,
                messages=[{"role": "user", "content": text}],
                max_tokens=5,
            )
        except LLMError:
            return False  # sentiment is best-effort; never block an answer on it
        return "NEGATIVE" in response.text.upper()

    def _finish(
        self,
        conversation_id: str,
        channel: Channel,
        customer_text: str,
        answer: str,
        hits: list[Hit],
        markers: list[str],
        used_grounded_tool: bool,
    ) -> AgentReply:
        cited = {f"S{n}" for n in CITATION.findall(answer)} & set(markers)
        if not answer or (not cited and not used_grounded_tool):
            return self._miss(conversation_id, channel, customer_text)
        clean = CITATION.sub("", answer).replace("  ", " ").strip()
        record_message(self.session, conversation_id, Role.agent, clean)
        sources = tuple(dict.fromkeys(hits[markers.index(m)].source_path for m in sorted(cited)))
        return AgentReply("answer", clean, sources)

    def _miss(self, conversation_id: str, channel: Channel, customer_text: str) -> AgentReply:
        """Low-confidence: clarify once, escalate on the second consecutive miss."""
        conv = get_or_create_conversation(self.session, conversation_id, Channel.webchat, "")
        agent_turns = [m for m in conv.messages if m.role is Role.agent]
        if agent_turns and agent_turns[-1].text == CLARIFY_TEXT:
            return self._escalate(
                conversation_id,
                channel,
                EscalationReason.repeated_failure,
                f"Two questions in a row the assistant could not answer. "
                f"Last: {policy.summarise(customer_text, 150)}",
            )
        record_message(self.session, conversation_id, Role.agent, CLARIFY_TEXT)
        return AgentReply("clarify", CLARIFY_TEXT)

    def _escalate(
        self,
        conversation_id: str,
        channel: Channel,
        reason: EscalationReason,
        summary: str,
        text: str | None = None,
    ) -> AgentReply:
        create_ticket(self.session, conversation_id, reason, summary, channel)
        message = text or HANDOFF_TEXT[reason]
        record_message(self.session, conversation_id, Role.agent, message)
        return AgentReply("escalated", message, escalation_reason=reason)
