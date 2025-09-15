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
            print(f"🔗 MCP 서버에 연결 중: {self.mcp_server_url}")
            async with streamablehttp_client(self.mcp_server_url) as (read, write, _):
                async with ClientSession(read, write) as mcp_session:
                    # MCP 세션 초기화
                    print("⚡ MCP 연결 및 도구 준비 중...")
                    await mcp_session.initialize()
                    
                    # 사용 가능한 도구 목록 가져오기
                    tools_response = await mcp_session.list_tools()
                    azure_tools = self._convert_to_azure_tools(tools_response.tools)
                    print(f"✅ MCP 도구 준비 완료 ({len(azure_tools)}개 도구)")
                    
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
                    print("✅ 에이전트 준비 완료")
                    
                    try:
                        # 대화 시작
                        print("💬 대화 시작...")
                        thread = client.threads.create()
                        client.messages.create(
                            thread_id=thread.id,
                            role="user",
                            content=user_message
                        )
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
            print(f"❌ 오류 발생: {str(e)}")
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
        
        max_iterations = 30  # 충분한 시간 확보
        iteration = 0
        
        print(f"🔄 에이전트 실행 모니터링 시작 (최대 {max_iterations}회)")
        
        while iteration < max_iterations:
            iteration += 1
            
            # 실행 상태 확인
            run = client.runs.get(thread_id=thread_id, run_id=run_id)
            
            if run.status in ("queued", "in_progress"):
                # 5회마다 또는 15회 이상에서만 로그 출력
                if iteration % 5 == 0 or iteration > 15:
                    print(f"⏳ 에이전트 처리 중... ({iteration}회)")
                await asyncio.sleep(1.0)
                continue
            
            elif run.status == "requires_action":
                print("🛠️ 도구 호출 필요")
                # 도구 호출 실행
                tool_calls = run.required_action.submit_tool_outputs.tool_calls
                print(f"📞 호출할 도구 수: {len(tool_calls)}")
                tool_outputs = []
                
                for i, tool_call in enumerate(tool_calls, 1):
                    if i == 1:  # 첫 번째 도구만 로그
                        print(f"  🔧 {tool_call.function.name} 실행 중...")
                    
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
                
                await asyncio.sleep(0.5)  # 대기 시간 단축
                continue
            
            elif run.status == "completed":
                print("🎉 실행 완료!")
                # 완료된 경우 최종 응답 반환
                messages = client.messages.list(thread_id=thread_id)
                assistant_messages = [
                    msg for msg in messages 
                    if msg.role == "assistant" and msg.text_messages
                ]
                
                if assistant_messages:
                    final_response = assistant_messages[-1].text_messages[-1].text.value
                    return final_response
                else:
                    return "응답을 생성할 수 없습니다."
            
            else:
                # 실패하거나 기타 상태
                print(f"❌ 실행 실패 또는 기타 상태: {run.status}")
                if hasattr(run, 'last_error') and run.last_error:
                    print(f"   🔍 오류 세부사항: {run.last_error}")
                    print(f"   📋 오류 코드: {getattr(run.last_error, 'code', 'N/A')}")
                    print(f"   📝 오류 메시지: {getattr(run.last_error, 'message', 'N/A')}")
                

                
                return f"실행이 실패했습니다. 상태: {run.status}"
        
        # 최대 반복 횟수 초과 시 마지막 상태 확인
        final_run = client.runs.get(thread_id=thread_id, run_id=run_id)
        print(f"⚠️ 최대 반복 횟수 초과 (마지막 상태: {final_run.status})")
        
        # 마지막 상태가 completed라면 응답 가져오기 시도
        if final_run.status == "completed":
            print("🎯 마지막 순간에 완료됨! 응답 가져오는 중...")
            messages = client.messages.list(thread_id=thread_id)
            assistant_messages = [
                msg for msg in messages 
                if msg.role == "assistant" and msg.text_messages
            ]
            
            if assistant_messages:
                final_response = assistant_messages[-1].text_messages[-1].text.value
                return final_response
        
        return f"최대 반복 횟수({max_iterations})를 초과했습니다. 마지막 상태: {final_run.status}"
    
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
            
            final_result = "\n".join(text_results) if text_results else "결과가 없습니다."
            
            return final_result
            
        except Exception as e:
            error_msg = f"도구 실행 오류: {str(e)}"
            print(f"❌ {error_msg}")
            return error_msg


async def main():
    """메인 실행 함수"""
    
    print("🚀 Microsoft Learn MCP 클라이언트 시작")
    print("=" * 60)
    
    client = MSLearnMCPClient()
    
    # 테스트 메시지
    test_message = (
        "Azure AI Foundry Agent Service에 대해 설명해줘. "
        "특히 MCP 서버를 연동해서 개발하는 방법에 대한 정보를 찾아줘"
    )
    
    print(f"🌐 MCP 서버 URL: {client.mcp_server_url}")
    print(f"❓ 질문: {test_message}")
    print("-" * 60)
    
    response = await client.connect_and_run(test_message)
    
    print("=" * 60)
    print("📄 최종 응답:")
    print("-" * 60)
    print(response)
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())