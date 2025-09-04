# Azure AI Foundry SDK 실습: Single Agent / Multi Agent (Python)

이 저장소는 Azure AI Foundry Agent Service의 Python SDK로 단일 에이전트와 다중 에이전트(두 에이전트 협업)를 구현하는 실습 가이드와 예제 코드를 포함합니다.

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
/ai-foundry-labs
├─ README.md
├─ requirements.txt
├─ .env.example
├─ .gitignore
├─ single_agent.py       # 단일 에이전트 실습
└─ multi_agents.py       # 두 에이전트 협업 실습
```

## 빠른 시작(공통)
0) 파이썬 설치/확인(macOS)
- 버전 확인
```zsh
python3 --version
```
- 미설치 시(Homebrew 사용)
```zsh
brew install python
```
- pip 업그레이드(선택)
```zsh
python3 -m pip install --upgrade pip
```

1) 로그인
- `az login`

2) 파이썬 가상환경(venv) 설정 - macOS zsh
- 권장: 프로젝트별 가상환경 사용(최초 1회 생성)
```zsh
python3 -m venv .venv
```
- 활성화(매 세션마다)
```zsh
source .venv/bin/activate
```
- 비활성화
```zsh
deactivate
```
참고
- 터미널을 새로 열 때마다 `source .venv/bin/activate`로 재활성화하세요.
- `python` 명령이 2.x를 가리킨다면 `python3`를 사용하세요.

3) 환경 변수 설정(zsh)
- 예시 파일 복사 후 값 채우기
```zsh
cp .env.example .env
```
- `.env` 편집 후 로드
```zsh
source .env
```

4) 패키지 설치
```zsh
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Lab 1: Single Agent
- 목표: Code Interpreter 툴을 사용해 간단한 수학/그래프 요청을 처리하는 에이전트 생성 및 실행

실행
- `python single_agent.py`

예상 흐름
- 에이전트 생성 → 스레드 생성 → 사용자 질문 추가 → 실행 완료 대기 → 메시지 로그 출력

## Lab 2: Multi Agent(두 에이전트 협업)
- 목표: 동일한 Thread를 공유하는 두 에이전트(Researcher, Writer)가 순차로 협업
- Researcher: 분석 및 구조화 → Writer: 경영진 요약/권고안 작성

실행
- `python multi_agents.py`

예상 흐름
- 두 에이전트 생성 → Thread 공유 → 사용자 과업 → Researcher 실행 → Writer에 핸드오프 메시지 → Writer 실행 → 결과 확인

## Lab 3: MCP 서버 연동 예제
- 파일: `mcp_bridge_example.py`
- 목적: Function Tool을 통해 모델이 MCP 서버의 툴을 호출하도록 브리지
- 추가 의존성: `pip install mcp` (requirements.txt 포함)
- 환경 변수(선택):
  - `MCP_SERVER_CMD` (예: 로컬 MCP 서버 실행 커맨드)
  - `MCP_SERVER_ARGS` (공백으로 구분된 인자 문자열)
  - `MCP_TOOL_NAME` (서버의 호출할 툴명, 기본값: `search`)
- 실행
```zsh
python mcp_bridge_example.py
```

# AI Foundry Labs - MCP Bridge

This lab shows how to:
- Run a simple MCP server using FastMCP (streamable HTTP)
- Connect to it remotely from an Azure AI Agents SDK client that maps MCP tools to function tools
- Use National Weather Service (api.weather.gov) tools exposed by MCP

## Requirements

- Python 3.10+
- `pip install -r requirements.txt`
- Azure credentials available to `DefaultAzureCredential` (e.g., `az login`)
- An Azure AI Foundry Project and a model deployment

## Environment

Create `.env` by copying `.env.example` and fill values:

- `PROJECT_ENDPOINT` (e.g., https://YOUR_PROJECT_REGION.projects.ai.azure.com)
- `MODEL_DEPLOYMENT_NAME` (your model deployment name)
- Optional: `MCP_SERVER_URL` (default: http://127.0.0.1:8765/mcp)
- Recommended: `NWS_USER_AGENT` (per NWS policy include contact info)

The bridge auto-loads `.env`.

## Run MCP Server

```
python mcp_server.py
```
Server runs on `http://127.0.0.1:8765/mcp`.

### Weather tools (NWS)
- `nws_point(lat, lon)`: grid/forecast URL, 위치 요약
- `nws_forecast(lat, lon, hourly=False)`: 예보 요약(일별/시간별)
- `nws_alerts(area=None, zone=None, limit=10)`: 경보 조회

## Run Bridge Example

```
python mcp_bridge_example.py
```

The bridge will:
- Discover remote MCP tools (including NWS)
- Create an Azure Agent with mapped tools
- Send a demo message
- Handle tool calls by invoking the MCP tools
- Print the conversation

## Lab 4: Agent-to-Agent Orchestrator (Remote A2A)
원격 A2A HTTP 서버(Title / Outline 등)를 Orchestrator 에이전트가 발견하고 라우팅하는 패턴을 실습합니다.

### Orchestrator 구조
- Orchestrator 에이전트: FunctionTool(`delegate_to_agent`) 호출 → 원격 에이전트 선택
- 원격 에이전트 서버: A2A 프로토콜로 `AgentCard`/`AgentSkill` 제공 (Starlette + uvicorn)
- 라우팅 로직: `requires_action` 상태에서 delegate_to_agent 호출 파라미터 분석 후 HTTP 전송 → 응답을 tool output으로 제출

### 구성 파일
- 파일: `a2a_orchestrator.py`
- 원격 서버: `a2a_servers/title_agent/server.py`, `a2a_servers/outline_agent/server.py`

### 목적
- 다수의 특화 에이전트를 느슨하게(HTTP) 연결하는 Orchestrator 패턴 이해
- 모델이 직접 어떤 원격 에이전트를 사용할지 판단하고 tool call 형태로 요청

#### 실행 순서 (필수 프로세스)
Terminal 1 (Title Agent)
```zsh
source .env
export TITLE_AGENT_PORT=8001
python -m a2a_servers.title_agent.server
```
Terminal 2 (Outline Agent)
```zsh
source .env
export OUTLINE_AGENT_PORT=8002
python -m a2a_servers.outline_agent.server
```
Terminal 3 (Orchestrator)
```zsh
source .env
export REMOTE_AGENT_URLS="http://localhost:8001,http://localhost:8002"
python a2a_orchestrator.py
```

필수 환경 변수:
- `PROJECT_ENDPOINT`, `MODEL_DEPLOYMENT_NAME`
선택/라우팅: `REMOTE_AGENT_URLS` (또는 `TITLE_AGENT_URL`, `OUTLINE_AGENT_URL`)

정상 로그 기대:
- Discovered agent ... (각 서버)
- Available remote agents: ...
- Routed to '...'
- 마지막에 최종 응답 출력 후 orchestrator 삭제 로그

문제 해결(핵심):
- No remote agents → URL env 미설정 또는 서버 미기동
- Failed to resolve AgentCard → 서버 포트/health 확인(`curl http://localhost:8001/health`)
- Unknown agent → 모델이 다른 이름 사용 → Orchestrator instructions에 발견된 이름 포함됨 확인

## 문제 해결
- 권한 오류: 프로젝트 범위 Azure AI User 역할을 확인하세요.
- 엔드포인트 형식: 프로젝트 엔드포인트(프로젝트 기반 URL)인지 재확인하세요.
- 모델 이름: 코드에서 사용하는 값은 “모델 배포 이름”입니다(기저 모델명과 다를 수 있음).
- Code Interpreter 출력 파일: 이미지/파일이 생성될 수 있습니다. 필요한 경우 SDK의 files API로 다운로드할 수 있습니다(본 예제는 텍스트 로그 중심).

## 참고 문서
- 개요: https://learn.microsoft.com/azure/ai-foundry/agents/overview
- 퀵스타트(Python): https://learn.microsoft.com/azure/ai-foundry/agents/quickstart?tabs=python
- Python 레퍼런스(AgentsClient): https://learn.microsoft.com/python/api/azure-ai-agents/azure.ai.agents.agentsclient
