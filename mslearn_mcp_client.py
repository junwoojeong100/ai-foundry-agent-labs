"""
Microsoft Learn MCP 서버 연결 클라이언트

이 스크립트는 Microsoft Learn MCP 서버에 연결하여 Azure AI Agents와 브리지합니다.
- MCP 서버 URL: https://learn.microsoft.com/api/mcp
- 서버 라벨: mslearn
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List

# 환경 변수 로드
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Azure AI Agents SDK
from azure.identity import DefaultAzureCredential
from azure.ai.agents import AgentsClient

# MCP 클라이언트
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp import types as mcp_types


class MSLearnMCPClient:
    """Microsoft Learn MCP 서버 클라이언트"""
    
    def __init__(self):
        self.mcp_server_url = "https://learn.microsoft.com/api/mcp"
        self.mcp_server_label = "mslearn"
        
        # Azure AI 설정
        self.project_endpoint = os.environ.get("PROJECT_ENDPOINT")
        self.model_deployment_name = os.environ.get("MODEL_DEPLOYMENT_NAME")
        
        if not self.project_endpoint or not self.model_deployment_name:
            raise ValueError(
                "PROJECT_ENDPOINT와 MODEL_DEPLOYMENT_NAME 환경변수를 설정해주세요"
            )
    
    async def connect_and_run(self, user_message: str) -> str:
        """MCP 서버에 연결하고 사용자 메시지를 처리합니다"""
        
        try:
            # MCP 서버 연결
            async with streamablehttp_client(self.mcp_server_url) as (read, write, _):
                async with ClientSession(read, write) as mcp_session:
                    # MCP 세션 초기화
                    await mcp_session.initialize()
                    
                    # 사용 가능한 도구 목록 가져오기
                    tools_response = await mcp_session.list_tools()
                    azure_tools = self._convert_to_azure_tools(tools_response.tools)
                    
                    # Azure AI Agents 클라이언트 생성
                    client = AgentsClient(
                        endpoint=self.project_endpoint, 
                        credential=DefaultAzureCredential()
                    )
                    
                    # 에이전트 생성
                    agent = client.create_agent(
                        model=self.model_deployment_name,
                        name=f"mslearn-mcp-agent",
                        instructions=(
                            "당신은 Microsoft Learn 문서에 접근할 수 있는 AI 도우미입니다. "
                            "제공된 도구를 사용하여 Microsoft 기술 문서를 검색하고 "
                            "사용자의 질문에 정확하고 도움이 되는 답변을 제공하세요."
                        ),
                        tools=azure_tools,
                    )
                    
                    try:
                        # 대화 스레드 생성 및 메시지 추가
                        thread = client.threads.create()
                        client.messages.create(
                            thread_id=thread.id,
                            role="user",
                            content=user_message
                        )
                        
                        # 실행 시작
                        run = client.runs.create(thread_id=thread.id, agent_id=agent.id)
                        
                        # 도구 호출 처리
                        response = await self._handle_tool_calls(
                            client, mcp_session, thread.id, run.id
                        )
                        
                        return response
                        
                    finally:
                        # 에이전트 정리
                        try:
                            client.delete_agent(agent.id)
                        except Exception:
                            pass
                            
        except Exception as e:
            return f"오류가 발생했습니다: {str(e)}"
    
    def _convert_to_azure_tools(self, mcp_tools: List[Any]) -> List[Dict[str, Any]]:
        """MCP 도구를 Azure AI Agents 형식으로 변환"""
        azure_tools = []
        
        for tool in mcp_tools:
            azure_tool = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or f"{self.mcp_server_label} 도구: {tool.name}",
                    "parameters": tool.inputSchema or {
                        "type": "object", 
                        "properties": {}
                    },
                },
            }
            azure_tools.append(azure_tool)
        
        return azure_tools
    
    async def _handle_tool_calls(
        self, 
        client: AgentsClient, 
        mcp_session: ClientSession,
        thread_id: str, 
        run_id: str
    ) -> str:
        """도구 호출을 처리하고 최종 응답을 반환"""
        
        max_iterations = 10
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            
            # 실행 상태 확인
            run = client.runs.get(thread_id=thread_id, run_id=run_id)
            
            if run.status in ("queued", "in_progress"):
                await asyncio.sleep(1.0)
                continue
            
            elif run.status == "requires_action":
                # 도구 호출 실행
                tool_calls = run.required_action.submit_tool_outputs.tool_calls
                tool_outputs = []
                
                for tool_call in tool_calls:
                    output = await self._execute_mcp_tool(
                        mcp_session, tool_call
                    )
                    tool_outputs.append({
                        "tool_call_id": tool_call.id,
                        "output": output
                    })
                
                # 도구 실행 결과 제출
                client.runs.submit_tool_outputs(
                    thread_id=thread_id,
                    run_id=run_id,
                    tool_outputs=tool_outputs,
                )
                
                await asyncio.sleep(1.0)
                continue
            
            elif run.status == "completed":
                # 완료된 경우 최종 응답 반환
                messages = client.messages.list(thread_id=thread_id)
                assistant_messages = [
                    msg for msg in messages 
                    if msg.role == "assistant" and msg.text_messages
                ]
                
                if assistant_messages:
                    return assistant_messages[-1].text_messages[-1].text.value
                else:
                    return "응답을 생성할 수 없습니다."
            
            else:
                # 실패하거나 기타 상태
                return f"실행이 실패했습니다. 상태: {run.status}"
        
        return "최대 반복 횟수를 초과했습니다."
    
    async def _execute_mcp_tool(
        self, 
        mcp_session: ClientSession, 
        tool_call: Any
    ) -> str:
        """MCP 도구를 실행하고 결과를 반환"""
        
        try:
            # 도구 인자 파싱
            arguments = json.loads(tool_call.function.arguments or "{}")
            
            # MCP 도구 호출
            result = await mcp_session.call_tool(
                tool_call.function.name, 
                arguments
            )
            
            # 텍스트 결과 추출
            text_results = []
            for content in result.content:
                if isinstance(content, mcp_types.TextContent):
                    text_results.append(content.text)
            
            return "\n".join(text_results) if text_results else "결과가 없습니다."
            
        except Exception as e:
            return f"도구 실행 오류: {str(e)}"


async def main():
    """메인 실행 함수"""
    
    client = MSLearnMCPClient()
    
    # 테스트 메시지
    test_message = (
        "Azure AI Foundry Agent Service에 대해 설명해줘. "
        "특히 MCP 서버를 연동해서 개발하는 방법에 대한 정보를 찾아줘"
    )
    
    print(f"Microsoft Learn MCP 서버에 연결 중...")
    print(f"서버 URL: {client.mcp_server_url}")
    print(f"질문: {test_message}")
    print("-" * 80)
    
    response = await client.connect_and_run(test_message)
    print(f"응답:\n{response}")


if __name__ == "__main__":
    asyncio.run(main())