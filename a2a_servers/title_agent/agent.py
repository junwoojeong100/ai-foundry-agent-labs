"""TitleAgent that runs on Azure AI Agents to generate a blog post title."""
import os
from typing import List
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.agents import AgentsClient
from azure.ai.agents.models import ListSortOrder, MessageRole

load_dotenv()

PROJECT_ENDPOINT = os.environ.get("PROJECT_ENDPOINT")
MODEL_DEPLOYMENT_NAME = os.environ.get("MODEL_DEPLOYMENT_NAME")


class TitleAgent:
    def __init__(self) -> None:
        if not PROJECT_ENDPOINT or not MODEL_DEPLOYMENT_NAME:
            raise RuntimeError("PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME must be set in environment.")
        self.client = AgentsClient(
            endpoint=PROJECT_ENDPOINT,
            credential=DefaultAzureCredential(
                exclude_environment_credential=True,
                exclude_managed_identity_credential=True,
            ),
        )
        self.agent = None

    async def create_agent(self):
        if self.agent:
            return self.agent
        self.agent = self.client.create_agent(
            model=MODEL_DEPLOYMENT_NAME,
            name="title-agent",
            instructions=(
                "You are a helpful writing assistant. Given a topic,"
                " suggest a single clear and catchy blog post title."
            ),
        )
        return self.agent

    async def run_conversation(self, user_message: str) -> List[str]:
        """Send the user message and return a list of assistant responses (strings)."""
        thread = self.client.threads.create()
        self.client.messages.create(thread_id=thread.id, role=MessageRole.USER, content=user_message)
        run = self.client.runs.create_and_process(thread_id=thread.id, agent_id=self.agent.id)
        if run.status == "failed":
            return [f"Run failed: {run.last_error}"]
        messages = self.client.messages.list(thread_id=thread.id, order=ListSortOrder.ASCENDING)
        result: List[str] = []
        for msg in messages:
            if msg.role == MessageRole.AGENT and msg.text_messages:
                result.append(msg.text_messages[-1].text.value)
        return result
