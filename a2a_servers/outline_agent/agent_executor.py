"""A2A executor wrapper for the OutlineAgent."""
from typing import List
from a2a.server.events.event_queue import EventQueue
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.tasks import TaskUpdater
from a2a.utils import new_agent_text_message
from a2a.types import AgentCard, Part, TaskState
from .agent import OutlineAgent


class OutlineAgentExecutor(AgentExecutor):
    def __init__(self, card: AgentCard):
        self._card = card
        self._foundry_agent: OutlineAgent | None = None

    async def _get_or_create_agent(self) -> OutlineAgent:
        if not self._foundry_agent:
            self._foundry_agent = OutlineAgent()
            await self._foundry_agent.create_agent()
        return self._foundry_agent

    async def _process_request(self, message_parts: List[Part], context_id: str, task_updater: TaskUpdater) -> None:
        text_inputs = [p.text for p in message_parts if getattr(p, "text", None)]
        user_text = "\n".join(text_inputs) if text_inputs else ""

        if not user_text.strip():
            await task_updater.complete(
                message=new_agent_text_message(
                    "No input text provided to Outline Agent. Please include a 'task' text describing the topic.",
                    context_id=context_id,
                )
            )
            return

        agent = await self._get_or_create_agent()

        await task_updater.update_status(
            TaskState.working,
            message=new_agent_text_message("Outline Agent is processing your request...", context_id=context_id),
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
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await self._process_request(context.message.parts, context.context_id, updater)

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


def create_outline_agent_executor(card: AgentCard) -> OutlineAgentExecutor:
    return OutlineAgentExecutor(card)
