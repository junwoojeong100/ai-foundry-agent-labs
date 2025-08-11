import os
from dotenv import load_dotenv
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.ai.agents.models import CodeInterpreterTool


def main():
    load_dotenv()  # load .env if present
    project_endpoint = os.environ.get("PROJECT_ENDPOINT")
    model = os.environ.get("MODEL_DEPLOYMENT_NAME")
    if not project_endpoint or not model:
        raise RuntimeError("Env vars PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME are required.")

    client = AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential())
    code_interpreter = CodeInterpreterTool()

    with client:
        # 1) 에이전트 생성
        agent = client.agents.create_agent(
            model=model,
            name="single-agent-demo",
            instructions=(
                "You politely help with math and data visualization. "
                "Use code when needed and return a brief explanation."
            ),
            tools=code_interpreter.definitions,
        )
        print(f"Agent created: {agent.id}")

        # 2) 스레드 생성
        thread = client.agents.threads.create()
        print(f"Thread created: {thread.id}")

        # 3) 사용자 메시지 추가
        client.agents.messages.create(
            thread_id=thread.id,
            role="user",
            content="직선 y = 4x + 9 의 그래프를 그려 PNG로 보여줘."
        )

        # 4) 실행(run) 처리
        run = client.agents.runs.create_and_process(thread_id=thread.id, agent_id=agent.id)
        print(f"Run status: {run.status}")
        if run.status == "failed":
            print(f"Run failed: {run.last_error}")

        # 5) 메시지 출력
        print("=== Messages ===")
        for msg in client.agents.messages.list(thread_id=thread.id):
            print(f"[{msg.role}] {msg.content}")

        # 6) 정리(원하면 유지)
        client.agents.delete_agent(agent.id)
        print("Agent deleted.")


if __name__ == "__main__":
    main()
