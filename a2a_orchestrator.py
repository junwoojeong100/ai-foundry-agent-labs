import os
import asyncio
import json
import uuid
import httpx
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import (
    ConnectedAgentToolDefinition,
    ConnectedAgentDetails,
    FunctionTool,
    MessageRole,
    ListSortOrder,
)

# Load .env if present so PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME are available
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

# A2A SDK imports (AgentSkill/AgentCard and client utilities)
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import AgentCard, AgentSkill, MessageSendParams, SendMessageRequest, SendMessageResponse


def ensure_env() -> tuple[str, str]:
    endpoint = os.environ.get("PROJECT_ENDPOINT")
    model = os.environ.get("MODEL_DEPLOYMENT_NAME")
    if not endpoint or not model:
        raise RuntimeError("Env vars PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME are required.")
    return endpoint, model


def get_remote_addresses() -> list[str]:
    """Get remote agent URLs from env. Supports REMOTE_AGENT_URLS (CSV) or TITLE_AGENT_URL/OUTLINE_AGENT_URL."""
    urls_csv = os.environ.get("REMOTE_AGENT_URLS", "").strip()
    urls: list[str] = [u.strip() for u in urls_csv.split(",") if u.strip()] if urls_csv else []
    for key in ("TITLE_AGENT_URL", "OUTLINE_AGENT_URL"):
        val = os.environ.get(key)
        if val:
            urls.append(val.strip())
    # De-duplicate, preserve order
    seen = set()
    uniq: list[str] = []
    for u in urls:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


class RemoteAgentConnection:
    """Holds A2A connection info for a remote agent (no persistent HTTP client)."""

    def __init__(self, card: AgentCard, url: str):
        self.card = card
        self.url = url


async def discover_remote_agents(urls: list[str]) -> dict[str, RemoteAgentConnection]:
    """Resolve AgentCard for each URL and return mapping by agent name."""
    result: dict[str, RemoteAgentConnection] = {}
    if not urls:
        print("[WARN] No remote agent URLs provided. Set REMOTE_AGENT_URLS or TITLE_AGENT_URL/OUTLINE_AGENT_URL.")
        return result

    for address in urls:
        try:
            async with httpx.AsyncClient(timeout=30) as http_client:
                resolver = A2ACardResolver(http_client, address)
                card: AgentCard = await resolver.get_agent_card()
            result[card.name] = RemoteAgentConnection(card, address)
            # Log discovered skills
            skill_names = ", ".join([s.name for s in (card.skills or [])]) if getattr(card, "skills", None) else "(none)"
            print(f"[A2A] Discovered agent: name='{card.name}', url={address}, skills=[{skill_names}]")
        except Exception as e:
            print(f"[ERROR] Failed to resolve AgentCard from {address}: {e}")
            print("[HINT] Make sure the server is running and /health returns 200. For title agent:"
                  " `python3 -m a2a_servers.title_agent.server` (port $TITLE_AGENT_PORT)")
    return result


def _name_matches_skill(card: AgentCard, keywords: list[str]) -> bool:
    try:
        for s in (card.skills or []):
            text = " ".join([s.id or "", s.name or "", " ".join(s.tags or [])]).lower()
            if any(k in text for k in keywords):
                return True
    except Exception:
        pass
    return False


def resolve_agent_name(requested: str, task: str, connections: dict[str, RemoteAgentConnection]) -> tuple[str | None, str]:
    """Resolve a model-provided agent_name to a discovered agent. Supports nicknames and task-based fallback."""
    if not connections:
        return None, "no-remote-agents"

    # Exact match first (case-insensitive)
    if requested:
        for name in connections.keys():
            if name.lower() == requested.lower():
                return name, "exact"

    # Nickname mapping commonly used in samples
    nick = (requested or "").lower()
    by_skill_title = [n for n, c in connections.items() if _name_matches_skill(c.card, ["title"])]
    by_skill_outline = [n for n, c in connections.items() if _name_matches_skill(c.card, ["outline"])]

    if nick in {"blogtitlegenerator", "title", "generate_blog_title"} and by_skill_title:
        return by_skill_title[0], "nickname:title"
    if nick in {"outlinecreator", "outline", "generate_outline"} and by_skill_outline:
        return by_skill_outline[0], "nickname:outline"

    # Task heuristic fallback
    t = (task or "").lower()
    if any(w in t for w in ["title", "headline"]) and by_skill_title:
        return by_skill_title[0], "heuristic:title"
    if "outline" in t and by_skill_outline:
        return by_skill_outline[0], "heuristic:outline"

    # If only one agent, just use it
    if len(connections) == 1:
        return next(iter(connections.keys())), "single"

    return None, "unresolved"


async def send_to_remote(connections: dict[str, RemoteAgentConnection], agent_name: str, task: str) -> dict:
    """Send a user task to the remote agent via A2A and return the raw response as dict."""
    conn = connections[agent_name]
    message_id = str(uuid.uuid4())
    payload: dict[str, object] = {
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": task}],
            "messageId": message_id,
        },
    }
    req = SendMessageRequest(id=message_id, params=MessageSendParams.model_validate(payload))
    async with httpx.AsyncClient(timeout=30) as http_client:
        client = A2AClient(http_client, conn.card, url=conn.url)
        # Call with positional argument to match current SDK signature
        resp: SendMessageResponse = await client.send_message(req)
    return resp.model_dump() if hasattr(resp, "model_dump") else {"result": str(resp)}


async def run_orchestrator(endpoint: str, model: str) -> None:
    # Discover remote agents via A2A AgentCard/AgentSkill
    addresses = get_remote_addresses()
    connections = await discover_remote_agents(addresses)
    if connections:
        available = ", ".join(connections.keys())
        print(f"[A2A] Available remote agents: {available}")
    else:
        print("[WARN] No remote agents discovered. Orchestrator will still run but cannot route.")

    # Orchestrator tool signature: let the model request routing via delegate_to_agent
    def delegate_to_agent(agent_name: str, task: str) -> str:  # Signature only for schema; execution handled manually
        """Delegate a task to a named remote agent over A2A and return its response."""
        return "(routed)"

    functions = FunctionTool({delegate_to_agent})

    client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())

    with client:
        # Include discovered names in the instructions so the model uses exact values
        discovered_names = ", ".join(connections.keys()) if connections else "(none)"
        orchestrator = client.agents.create_agent(
            model=model,
            name="a2a-orchestrator",
            instructions=(
                "You delegate tasks to remote agents discovered via A2A.\n"
                f"Known remote agents: {discovered_names}. Use delegate_to_agent(agent_name, task) with one of these exact names.\n"
                "If unsure which to use, first pick based on the user's request."
            ),
            tools=functions.definitions,
        )
        print(f"[LOG] Orchestrator agent created: id={orchestrator.id}")

        thread = client.agents.threads.create()
        client.agents.messages.create(
            thread_id=thread.id,
            role="user",
            content=(
                "Create a catchy blog title for 'React programming' and a short outline. "
                "Use the appropriate remote agent(s)."
            ),
        )

        run = client.agents.runs.create(thread_id=thread.id, agent_id=orchestrator.id)

        # Handle requires_action to actually invoke A2A remote agents
        while True:
            run = client.agents.runs.get(thread_id=thread.id, run_id=run.id)
            if run.status in ("queued", "in_progress"):
                await asyncio.sleep(0.5)
                continue
            if run.status == "requires_action":
                tool_calls = run.required_action.submit_tool_outputs.tool_calls
                outputs = []
                for tc in tool_calls:
                    if tc.function.name == "delegate_to_agent":
                        args = json.loads(tc.function.arguments or "{}")
                        requested = (args.get("agent_name") or "").strip()
                        task = args.get("task", "")
                        resolved, reason = resolve_agent_name(requested, task, connections)
                        if not resolved:
                            err = {
                                "error": "Unknown agent",
                                "requested": requested,
                                "known": list(connections.keys()),
                                "hint": "Set REMOTE_AGENT_URLS or use one of the discovered names."
                            }
                            outputs.append({"tool_call_id": tc.id, "output": json.dumps(err)})
                            print(f"[A2A] Route error: unknown agent '{requested}' ({reason})")
                            continue
                        try:
                            result_dict = await send_to_remote(connections, resolved, task)
                            if isinstance(result_dict, dict) and "error" in result_dict:
                                print(f"[A2A] Routed to '{resolved}' but remote returned error: {result_dict['error']}")
                            else:
                                print(f"[A2A] Routed to '{resolved}' -> OK ({reason})")
                            outputs.append({"tool_call_id": tc.id, "output": json.dumps(result_dict)})
                        except Exception as e:
                            outputs.append({"tool_call_id": tc.id, "output": json.dumps({"error": str(e)})})
                            print(f"[A2A] Route exception for '{resolved}': {e}")
                    else:
                        outputs.append({"tool_call_id": tc.id, "output": json.dumps({"error": "Unknown function"})})
                client.agents.runs.submit_tool_outputs(thread_id=thread.id, run_id=run.id, tool_outputs=outputs)
                await asyncio.sleep(0.5)
                continue
            break

        # Show last assistant response
        msgs = client.agents.messages.list(thread_id=thread.id, order=ListSortOrder.DESCENDING)
        for m in msgs:
            if m.role == MessageRole.AGENT and m.text_messages:
                print(m.text_messages[-1].text.value)
                break

        # Cleanup
        client.agents.delete_agent(orchestrator.id)
        print("[LOG] Deleted orchestrator agent")


def main():
    endpoint, model = ensure_env()
    asyncio.run(run_orchestrator(endpoint, model))


if __name__ == "__main__":
    main()
