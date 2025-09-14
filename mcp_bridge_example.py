"""
Minimal bridge: Connect to a local MCP server and expose its tools to an Azure AI Agents SDK agent.

이 스크립트는 다음을 아주 간단히 보여줍니다.
1) 로컬에서 실행 중인 MCP 서버(예: mcp_server.py)에 연결한다.
2) MCP 서버가 제공하는 도구 목록을 받아 Azure AI Agents의 "함수형 도구"로 그대로 노출한다.
3) 에이전트가 실행(run) 중 도구 호출이 필요하면(=requires_action), 이 스크립트가 MCP 도구를 대신 호출해 결과를 전달한다.
4) 마지막 어시스턴트 응답만 출력하고 종료한다.

사전 준비:
- 터미널 1: python3 ./mcp_server.py (MCP 서버 시작)
- 터미널 2: python3 ./mcp_bridge_example.py (이 브리지 실행)
- .env 파일에 아래 값 설정(없으면 환경변수로 설정):
  PROJECT_ENDPOINT, MODEL_DEPLOYMENT_NAME, (선택) MCP_SERVER_URL
"""
from __future__ import annotations

import asyncio
import json
import os

# (선택) .env 파일을 로드합니다. 실패해도 무시합니다. 초보자에게 편한 설정 방식입니다.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

# Azure AI Agents SDK 클라이언트를 사용하기 위한 인증 및 클라이언트
from azure.identity import DefaultAzureCredential
from azure.ai.agents import AgentsClient

# MCP 클라이언트: 로컬 MCP 서버(streamable HTTP)에 연결하고 도구를 호출하는데 사용됩니다.
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp import types as mcp_types


# 필수: Azure AI Foundry 프로젝트 엔드포인트와 모델 배포 이름
PROJECT_ENDPOINT = os.environ.get("PROJECT_ENDPOINT")
MODEL_DEPLOYMENT_NAME = os.environ.get("MODEL_DEPLOYMENT_NAME")
# 선택: MCP 서버 URL (mcp_server.py 기본값과 동일)
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:8765/mcp")


async def main_async() -> None:
    # 필수 환경변수 확인: 없으면 친절한 오류를 띄워, 어디를 고쳐야 하는지 알 수 있게 합니다.
    if not PROJECT_ENDPOINT or not MODEL_DEPLOYMENT_NAME:
        raise RuntimeError("Set PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME in .env or environment.")

    # 1) 로컬 MCP 서버에 연결합니다. streamablehttp_client 는 (read, write, close) 핸들을 제공합니다.
    async with streamablehttp_client(MCP_SERVER_URL) as (read, write, _):
        # ClientSession 은 MCP 프로토콜 대화를 관리합니다.
        async with ClientSession(read, write) as mcp_session:
            # initialize() 는 서버 기능(도구/리소스/프롬프트 등)을 사용하기 전에 반드시 호출해야 합니다.
            await mcp_session.initialize()

            # 2) MCP 서버가 노출하는 도구 목록을 가져와, Azure의 "function" 형식 도구로 변환합니다.
            #    - name/description/parameters 를 그대로 매핑합니다.
            tools = (await mcp_session.list_tools()).tools
            azure_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,  # 도구 이름(에이전트가 호출할 함수명)
                        "description": t.description or "",  # 간단 설명
                        # JSON Schema 형식의 파라미터 정의(모델이 호출 인자를 구성하는데 사용)
                        "parameters": t.inputSchema or {"type": "object", "properties": {}},
                    },
                }
                for t in tools
            ]

            # 3) Azure AI Agents 클라이언트를 만들고, 에이전트를 생성합니다.
            #    - instructions: 에이전트가 도구를 사용할 수 있음을 알려줍니다.
            client = AgentsClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())
            agent = client.create_agent(
                model=MODEL_DEPLOYMENT_NAME,
                name="mcp-bridge",
                instructions=(
                    "이 에이전트는 로컬 MCP 서버와 브리지된 도구들을 사용할 수 있습니다. "
                    "필요하면 해당 도구(function)를 호출하여 정보를 얻고, 결과를 바탕으로 사용자 질문에 답변하세요."
                ),
                tools=azure_tools,
            )

            # 4) 쓰레드를 만들고 사용자 메시지를 추가합니다.
            #    - 이 예시는 샌프란시스코의 날씨 요약을 도구로 조회하도록 요청합니다.
            thread = client.threads.create()
            client.messages.create(
                thread_id=thread.id,
                role="user",
                content=(
                    "제공된 도구를 사용하여 샌프란시스코(San Francisco, CA: 위도 37.7749, 경도 -122.4194)의 "
                    "현재 일기예보 요약을 알려주세요. 가능하다면 다음 두 개 기간(period)의 예보도 함께 포함해 주세요."
                ),
            )

            # 5) 실행(run)을 시작합니다. 실행 상태는 polling 으로 확인합니다.
            run = client.runs.create(thread_id=thread.id, agent_id=agent.id)

            # 6) requires_action 루프: 모델이 도구 호출을 요구하면 MCP 도구를 대신 호출해 결과를 넣어줍니다.
            while True:
                # 최신 상태를 가져옵니다.
                run = client.runs.get(thread_id=thread.id, run_id=run.id)

                # a) 모델이 아직 생각(토큰 생성) 중이면 잠시 대기합니다.
                if run.status in ("queued", "in_progress"):
                    await asyncio.sleep(0.5)
                    continue

                # b) 도구 호출이 필요하면(모델이 함수 호출을 계획함), 우리가 MCP 도구를 호출해서 결과를 제출합니다.
                if run.status == "requires_action":
                    calls = run.required_action.submit_tool_outputs.tool_calls
                    outputs = []  # 각 호출의 결과를 담아 다시 모델에 제출합니다.

                    for tc in calls:
                        # 모델이 생성한 JSON 인자를 파싱합니다.
                        args = json.loads(tc.function.arguments or "{}")

                        # MCP 서버의 해당 도구를 실제로 호출합니다.
                        result = await mcp_session.call_tool(tc.function.name, args)

                        # 단순화를 위해 텍스트 결과만 모읍니다. (structuredContent 도 있을 수 있으나, 입문자 관점에서 생략)
                        text_parts = []
                        for c in result.content:
                            if isinstance(c, mcp_types.TextContent):
                                text_parts.append(c.text)

                        # Azure 쪽에 넘길 출력 문자열(여러 조각을 \n으로 합침)
                        outputs.append({
                            "tool_call_id": tc.id,  # 어떤 호출에 대한 결과인지 식별자
                            "output": "\n".join(text_parts).strip(),  # 모델에게 보여줄 텍스트
                        })

                    # 수집한 도구 결과를 모델에게 제출합니다. 모델은 이를 바탕으로 최종 답변을 완성합니다.
                    client.runs.submit_tool_outputs(
                        thread_id=thread.id,
                        run_id=run.id,
                        tool_outputs=outputs,
                    )

                    # 제출 직후 모델이 다시 생각을 이어가므로, 잠시 대기하고 다음 상태를 확인합니다.
                    await asyncio.sleep(0.5)
                    continue

                # c) 그 외 상태(예: completed/failed 등)면 루프를 종료합니다.
                break

            # 7) 대화가 끝났다면, 마지막 어시스턴트 메시지만 깔끔하게 출력합니다.
            msgs = client.messages.list(thread_id=thread.id)
            assistant_msgs = [m for m in msgs if m.role == "assistant" and m.text_messages]
            if assistant_msgs:
                print(assistant_msgs[-1].text_messages[-1].text.value)

            # 8) 사용이 끝난 에이전트는 정리합니다. (과금/리소스 관리 차원에서 권장)
            try:
                client.delete_agent(agent.id)
            except Exception:
                pass


# 표준 파이썬 엔트리포인트. asyncio 루프를 시작합니다.
def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
