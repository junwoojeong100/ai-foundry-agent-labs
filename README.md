# Azure AI Foundry Agents Labs (Python)

Azure AI Foundry Agent Service Python SDK를 이용한 핵심 패턴 실습: 단일 에이전트, 다중 에이전트 협업, MCP 서버 연동, 원격 Agent-to-Agent Orchestrator.

## 사전 준비
- Azure 구독 및 Azure AI Foundry 프로젝트
  - 포털: https://ai.azure.com 에서 프로젝트 생성
  - 프로젝트 개요에서 Project Endpoint 확인 (형식: `https://<ServiceName>.services.ai.azure.com/api/projects/<ProjectName>`)
  - Models + Endpoints에서 모델 배포(예: `gpt-4o` 또는 `gpt-4o-mini`) 이름 확인
- 권한: 프로젝트 범위에 Azure AI User 역할 (agents/*/read, agents/*/action, agents/*/delete)
- 인증: 로컬에서 Azure CLI 로그인(나중에 빠른 시작 단계에서 실행)
- Python: 3.9 이상 권장
- 환경 변수 값 준비: `PROJECT_ENDPOINT`, `MODEL_DEPLOYMENT_NAME` (설정은 아래 빠른 시작 단계 참고)

중요: 2025-05부터 에이전트 서비스는 Foundry “프로젝트” 엔드포인트를 사용합니다. 이전 허브 기반 연결 문자열은 최신 SDK에서 동작하지 않습니다.

## 프로젝트 구조
```
/ai-foundry-agent-labs
├─ README.md
├─ requirements.txt
├─ .env.example
├─ single_agent.py            # Lab 1: Single Agent
├─ multi_agents.py            # Lab 2: Multi Agent (Thread 공유)
├─ mcp_server.py              # Lab 3: MCP Demo Server
├─ mcp_bridge_example.py      # Lab 3: MCP Bridge Client
├─ a2a_orchestrator.py        # Lab 4: Remote A2A Orchestrator
└─ a2a_servers/               # Remote A2A 에이전트 서버 구현
  ├─ title_agent/
  └─ outline_agent/
```

## 빠른 시작 (공통)
1) Python & 로그인
```zsh
python3 --version      # 3.9 이상 권장 (3.10+ 테스트됨)
az login               # Azure 자격 증명
```
2) 가상환경
```zsh
python3 -m venv .venv
source .venv/bin/activate
```
3) 환경 변수 (.env)
```zsh
cp .env.example .env   # 이미 있으면 생략
# .env 편집 후
source .env
```
필수: `PROJECT_ENDPOINT`, `MODEL_DEPLOYMENT_NAME`

4) 패키지 설치
```zsh
pip install -r requirements.txt
```

5) 테스트 (Single Agent)
```zsh
python single_agent.py
```

## Lab 1: Single Agent
- 목표: Code Interpreter 툴을 사용해 단일 에이전트 생성 / 실행 흐름 이해
```zsh
python single_agent.py
```
흐름: 에이전트 생성 → Thread 생성 → 메시지 추가 → Run 처리 → 메시지 로깅

## Lab 2: Multi Agent (협업)
- 목표: 하나의 Thread를 공유하며 역할 분리(Researcher → Writer) 협업
```zsh
python multi_agents.py
```
핵심: 별도 Thread 복제 없이 메시지 컨텍스트 공유로 역할 전환

## Lab 3: MCP Bridge (외부 도구 호출)
- 목표: MCP 서버에서 제공하는 도구를 Function Tool 형태로 브릿징하여 에이전트가 호출
- 구성 파일:
  - 서버: `mcp_server.py`
  - 브리지 클라이언트: `mcp_bridge_example.py`
```zsh
# (터미널 1) MCP 서버
python mcp_server.py

# (터미널 2) 브리지 실행
python mcp_bridge_example.py
```
선택 환경 변수: `MCP_SERVER_URL`, `NWS_USER_AGENT`
동작: MCP Tool Discovery → Azure Agent 생성(맵핑된 툴) → 메시지 전송 → Tool Call → 결과 출력

## Lab 4: Remote Agent-to-Agent Orchestrator
원격 HTTP 에이전트들을 발견(AgentCard) 후 Function Tool 호출을 실제 HTTP 라우팅으로 처리.

구성:
- Orchestrator: `a2a_orchestrator.py`
- Remote Agents: `a2a_servers/title_agent/server.py`, `a2a_servers/outline_agent/server.py`

실행 (3 터미널 예)
```zsh
# Title Agent
source .env
export TITLE_AGENT_PORT=8001
python -m a2a_servers.title_agent.server

# Outline Agent
source .env
export OUTLINE_AGENT_PORT=8002
python -m a2a_servers.outline_agent.server

# Orchestrator
source .env
export REMOTE_AGENT_URLS="http://localhost:8001,http://localhost:8002"
python a2a_orchestrator.py
```
필수: `PROJECT_ENDPOINT`, `MODEL_DEPLOYMENT_NAME`
선택: `REMOTE_AGENT_URLS` (또는 개별 `TITLE_AGENT_URL`, `OUTLINE_AGENT_URL`)

기대 로그: Discovered agent → Available remote agents → Routed to '...' → 최종 응답 → 삭제 로그

문제 해결 요약:
- No remote agents → URL env 재확인 (`REMOTE_AGENT_URLS`)
- Resolve 실패 → 서버 기동/포트/health(`curl :8001/health`)
- Unknown agent → 모델이 제시한 이름과 발견 목록 불일치 (instructions 조정)

## 문제 해결 (공통)
- 권한 오류 → 프로젝트 범위 Azure AI User 역할 부여
- 401/404 → `PROJECT_ENDPOINT` 프로젝트 URL 형식 재확인
- 모델 미인식 → 실제 "배포 이름" 사용 여부 점검 (기저 모델명과 다를 수 있음)
- 장시간 대기 → Run status poll (in_progress 고착 시 네트워크/툴 실패 가능)
- Tool 호출 실패 → required_action 의 tool_calls 파라미터(JSON) 구조 확인
- Code Interpreter 산출물 → files API 사용해 필요 시 수집 (본 실습은 텍스트 중심)

## 참고 문서
- 개요: https://learn.microsoft.com/azure/ai-foundry/agents/overview
- 퀵스타트(Python): https://learn.microsoft.com/azure/ai-foundry/agents/quickstart?tabs=python
- Python 레퍼런스: https://learn.microsoft.com/python/api/azure-ai-agents/azure.ai.agents.agentsclient
- Azure Identity: https://learn.microsoft.com/python/api/overview/azure/identity-readme
