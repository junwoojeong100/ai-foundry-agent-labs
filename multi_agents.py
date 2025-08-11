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
        # 1) 두 에이전트 생성
        researcher = client.agents.create_agent(
            model=model,
            name="researcher",
            instructions=(
                "You are a detail-oriented research agent. Analyze data and produce structured findings. "
                "Use code for analysis when needed."
            ),
            tools=code_interpreter.definitions,
        )
        writer = client.agents.create_agent(
            model=model,
            name="writer",
            instructions=(
                "You are a clear and concise writer. Given research findings in the thread, "
                "produce a polished executive summary with action items."
            ),
            tools=code_interpreter.definitions,
        )
        print(f"Researcher: {researcher.id}, Writer: {writer.id}")

        # 2) Thread 공유
        thread = client.agents.threads.create()
        print(f"Thread: {thread.id}")

        # 3) 사용자 과업(연구원 담당)
        client.agents.messages.create(
            thread_id=thread.id,
            role="user",
            content=(
                "샘플 CSV가 있다고 가정하고, 간단한 시계열 분석을 수행해 핵심 인사이트 3가지를 찾아줘. "
                "필요하면 코드로 계산해도 좋아."
            ),
        )

        # 4) 연구원 실행
        run1 = client.agents.runs.create_and_process(thread_id=thread.id, agent_id=researcher.id)
        print(f"Researcher run status: {run1.status}")
        if run1.status == "failed":
            print(f"Researcher failed: {run1.last_error}")

        # 5) 작성자에게 핸드오프 메시지
        client.agents.messages.create(
            thread_id=thread.id,
            role="user",
            content=(
                "Writer, 위 연구 결과를 바탕으로 임원용 5문장 요약과 행동요령 3가지를 작성해줘. "
                "불확실한 부분은 명확히 표시해줘."
            ),
        )

        # 6) 작성자 실행
        run2 = client.agents.runs.create_and_process(thread_id=thread.id, agent_id=writer.id)
        print(f"Writer run status: {run2.status}")
        if run2.status == "failed":
            print(f"Writer failed: {run2.last_error}")

        # 7) 최종 메시지 로그
        print("=== Messages ===")
        for msg in client.agents.messages.list(thread_id=thread.id):
            print(f"[{msg.role}] {msg.content}")

        # 8) 정리(원하면 유지)
        client.agents.delete_agent(researcher.id)
        client.agents.delete_agent(writer.id)
        print("Agents deleted.")


if __name__ == "__main__":
    main()
