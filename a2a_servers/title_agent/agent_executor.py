"""A2A executor wrapper for the TitleAgent."""
from typing import List
from a2a.server.events.event_queue import EventQueue
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.tasks import TaskUpdater
from a2a.utils import new_agent_text_message
from a2a.types import AgentCard, Part, TaskState
from .agent import TitleAgent


class TitleAgentExecutor(AgentExecutor):
    def __init__(self, card: AgentCard):
        self._card = card
        self._foundry_agent: TitleAgent | None = None

    async def _get_or_create_agent(self) -> TitleAgent:
        if not self._foundry_agent:
            self._foundry_agent = TitleAgent()
            await self._foundry_agent.create_agent()
        return self._foundry_agent

    async def _process_request(self, message_parts: List[Part], context_id: str, task_updater: TaskUpdater) -> None:
        # Extract text parts
        text_inputs = [p.text for p in message_parts if getattr(p, "text", None)]
        user_text = "\n".join(text_inputs) if text_inputs else ""

        # If no text provided, return a friendly message instead of failing
        if not user_text.strip():
            await task_updater.complete(
                message=new_agent_text_message(
                    "No input text provided to Title Agent. Please include a 'task' text describing the topic.",
                    context_id=context_id,
                )
            )
            return

        agent = await self._get_or_create_agent()

        await task_updater.update_status(
            TaskState.working,
            message=new_agent_text_message("Title Agent is processing your request...", context_id=context_id),
        )
        responses = await agent.run_conversation(user_text)
        for r in responses:
            await task_updater.update_status(
                TaskState.working,
                message=new_agent_text_message(r, context_id=context_id),
            )
        final = responses[-1] if responses else "Task completed."
        await task_updater.complete(message=new_agent_text_message(final, context_id=context_id))

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        # Current a2a-sdk expects task_id and context_id
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await self._process_request(context.message.parts, context.context_id, updater)

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        # No-op cancel for this simple executor
        pass


def create_title_agent_executor(card: AgentCard) -> TitleAgentExecutor:
    return TitleAgentExecutor(card)
