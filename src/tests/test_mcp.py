import httpx
import pytest

from mcp import McpWorkflowClient
from schemas import StructuredContent


@pytest.mark.asyncio
async def test_mcp_workflow_client_calls_tool_and_returns_structured_content_model() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer workflow-token"
        assert request.headers["accept"] == "application/json"
        payload = {
            "result": {
                "content": [],
                "structuredContent": {
                    "workflow_name": "analyze_daily_log_bundle",
                    "prompt": "Log Summary Instructions",
                    "mandatory_skills": [
                        {
                            "skill_name": "project_context",
                            "resource_uri": "workflow/project_context",
                        }
                    ],
                    "optional_skills": [],
                    "tools": [],
                },
            }
        }
        return httpx.Response(200, json=payload)

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
    )

    structured_content: StructuredContent = await client.call_tool("analyze_daily_log_bundle")

    assert structured_content.workflow_name == "analyze_daily_log_bundle"
    assert structured_content.mandatory_skills[0].name == "project_context"
    assert len(requests) == 1
    request_payload = httpx.Request(
        "POST",
        "http://mcp.local/mcp",
        content=requests[0].content,
    ).read()
    assert b'"method":"tools/call"' in request_payload
    assert b'"name":"analyze_daily_log_bundle"' in request_payload


@pytest.mark.asyncio
async def test_mcp_workflow_client_get_workflow_bundle_uses_bootstrap_tool() -> None:
    tool_names: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = httpx.Request(
            "POST",
            "http://mcp.local/mcp",
            content=request.content,
        ).read()
        tool_names.append(payload.decode())
        return httpx.Response(
            200,
            json={
                "result": {
                    "structuredContent": {
                        "workflow_name": "analyze_daily_log_bundle",
                        "prompt": "Log Summary Instructions",
                        "mandatory_skills": [
                            {
                                "skill_name": "project_context",
                                "resource_uri": "workflow/project_context",
                            }
                        ],
                        "optional_skills": [],
                        "tools": [],
                    },
                }
            },
        )

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
    )

    workflow = await client.get_workflow_bundle()

    assert workflow.workflow_name == "analyze_daily_log_bundle"
    assert workflow.mandatory_skills[0].name == "project_context"
    assert '"name":"analyze_daily_log_bundle"' in tool_names[0]


@pytest.mark.asyncio
async def test_mcp_workflow_client_get_service_status_uses_status_tool() -> None:
    tool_names: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = httpx.Request(
            "POST",
            "http://mcp.local/mcp",
            content=request.content,
        ).read()
        tool_names.append(payload.decode())
        return httpx.Response(
            200,
            json={
                "result": {
                    "structuredContent": {
                        "name": "mcp-log-server",
                        "status": "ok",
                    },
                }
            },
        )

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
    )

    status = await client.get_service_status()

    assert status.status == "ok"
    assert '"name":"get_mcp_service_status"' in tool_names[0]


@pytest.mark.asyncio
async def test_mcp_workflow_client_requires_workflow_jwt() -> None:
    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )

    with pytest.raises(RuntimeError, match="MCP_WORKFLOW_JWT is required"):
        await client.call_tool("analyze_daily_log_bundle")


@pytest.mark.asyncio
async def test_mcp_workflow_client_raises_for_json_rpc_error() -> None:
    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"error": {"message": "denied"}})
        ),
    )

    with pytest.raises(RuntimeError, match="MCP workflow error"):
        await client.call_tool("analyze_daily_log_bundle")


@pytest.mark.asyncio
async def test_mcp_workflow_client_raises_for_missing_structured_content() -> None:
    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"result": {"content": []}})
        ),
    )

    with pytest.raises(RuntimeError, match="expected shape"):
        await client.call_tool("analyze_daily_log_bundle")


@pytest.mark.asyncio
async def test_mcp_workflow_client_raises_for_non_object_response() -> None:
    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
    )

    with pytest.raises(RuntimeError, match="must be a JSON object"):
        await client.call_tool("analyze_daily_log_bundle")
