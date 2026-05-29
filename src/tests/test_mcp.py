import json

import httpx
import pytest

from exceptions import McpClientError
from mcp import McpWorkflowClient
from schemas import CollectLogsArtifact, ProjectManifestSummary, StructuredContent
from tests.conftest import build_collect_logs_artifact_payload


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
async def test_mcp_workflow_client_get_sitemap_workflow_bundle_uses_bootstrap_tool() -> None:
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
                        "workflow_name": "analyze_sitemap_bundle",
                        "prompt": "Sitemap Summary Instructions",
                        "mandatory_skills": [],
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

    workflow = await client.get_sitemap_workflow_bundle()

    assert workflow.workflow_name == "analyze_sitemap_bundle"
    assert workflow.prompt == "Sitemap Summary Instructions"
    assert '"name":"analyze_sitemap_bundle"' in tool_names[0]


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
async def test_mcp_workflow_client_collect_logs_omits_project_names_for_jwt_scope() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "result": {
                    "structuredContent": build_collect_logs_artifact_payload(),
                }
            },
        )

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
    )

    artifact: CollectLogsArtifact = await client.collect_logs(
        since="2026-05-19T00:00:00Z",
        until="2026-05-20T00:00:00Z",
    )

    assert artifact.action == "collect_logs"
    assert artifact.projects[0].snapshot_dir == "workflow/landingpage/latest"
    assert artifact.projects[0].sources[0].source_key == "backend"
    assert requests[0]["method"] == "tools/call"
    assert requests[0]["params"] == {
        "name": "collect_logs",
        "arguments": {
            "since": "2026-05-19T00:00:00Z",
            "until": "2026-05-20T00:00:00Z",
        },
    }


@pytest.mark.asyncio
async def test_mcp_workflow_client_collect_logs_raises_tool_error_message() -> None:
    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": "Unknown project 'landingpage'.",
                            }
                        ],
                        "structuredContent": {
                            "status": "error",
                            "error_code": "unknown_project",
                            "message": "Unknown project 'landingpage'.",
                            "retry_tips": ["Call list_projects."],
                            "details": {"requested_project_names": ["landingpage"]},
                        },
                        "isError": True,
                    }
                },
            )
        ),
    )

    with pytest.raises(McpClientError) as error_info:
        await client.collect_logs(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
        )

    assert "Unknown project 'landingpage'" in str(error_info.value)
    assert "Call list_projects" in str(error_info.value)
    assert error_info.value.tool_name == "collect_logs"


@pytest.mark.asyncio
async def test_mcp_workflow_client_collect_logs_validation_error_lists_fields() -> None:
    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "result": {
                        "structuredContent": {
                            "action": "collect_logs",
                            "workspace": "workflow",
                            "projects": [
                                {
                                    "project_name": "landingpage",
                                    "workspace": "workflow",
                                    "snapshot_dir": "workflow/landingpage/latest",
                                    "collected_at": "2026-05-20T00:01:00Z",
                                }
                            ],
                        },
                    }
                },
            )
        ),
    )

    with pytest.raises(McpClientError) as error_info:
        await client.collect_logs(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
        )

    message = str(error_info.value)
    assert "MCP collect_logs response did not match expected shape" in message
    assert "result.structuredContent.projects.0.requested_project_name" in message


@pytest.mark.asyncio
async def test_mcp_workflow_client_list_projects_uses_discovery_tool() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "result": {
                    "structuredContent": {
                        "result": [
                            {
                                "project_name": "landingpage",
                                "project_summary": "Landingpage project.",
                                "source_keys": ["backend", "nginx"],
                            },
                            {
                                "project_name": "shop",
                                "project_summary": "Shop project.",
                                "source_keys": ["backend"],
                            },
                        ],
                    },
                }
            },
        )

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
    )

    projects: list[ProjectManifestSummary] = await client.list_projects()

    assert [project.project_name for project in projects] == ["landingpage", "shop"]
    assert projects[0].source_keys == ["backend", "nginx"]
    assert requests[0]["params"] == {
        "name": "list_projects",
        "arguments": {},
    }


@pytest.mark.asyncio
async def test_mcp_workflow_client_list_projects_returns_empty_list() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "result": {
                    "structuredContent": {
                        "result": [],
                    },
                }
            },
        )

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
    )

    projects: list[ProjectManifestSummary] = await client.list_projects()

    assert projects == []
    assert requests[0]["params"] == {
        "name": "list_projects",
        "arguments": {},
    }


@pytest.mark.asyncio
async def test_mcp_workflow_client_reads_workflow_skill_resource() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "result": {
                    "contents": [
                        {
                            "uri": "skill://workflow/project_context",
                            "mimeType": "text/plain",
                            "text": "Project context skill body.",
                        }
                    ]
                }
            },
        )

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
    )

    skill_text: str = await client.read_resource("skill://workflow/project_context")

    assert skill_text == "Project context skill body."
    assert requests[0]["method"] == "resources/read"
    assert requests[0]["params"] == {"uri": "skill://workflow/project_context"}


@pytest.mark.asyncio
async def test_mcp_workflow_client_calls_deterministic_tool() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "result": {
                    "structuredContent": {
                        "action": "group_errors",
                        "project_name": "landingpage",
                        "groups": [{"message": "No repeated errors", "count": 0}],
                    }
                }
            },
        )

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
    )

    structured_content: dict[str, object] = await client.call_deterministic_tool(
        "group_errors",
        {"project_name": "landingpage"},
    )

    assert structured_content["action"] == "group_errors"
    assert structured_content["project_name"] == "landingpage"
    assert requests[0]["params"] == {
        "name": "group_errors",
        "arguments": {"project_name": "landingpage"},
    }


@pytest.mark.asyncio
async def test_mcp_workflow_client_deterministic_tool_raises_result_error() -> None:
    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "result": {
                        "isError": True,
                        "structuredContent": {
                            "status": "error",
                            "message": "Unknown source key 'backend'.",
                            "retry_tips": ["Call list_projects."],
                        },
                    }
                },
            )
        ),
    )

    with pytest.raises(McpClientError) as error_info:
        await client.call_deterministic_tool(
            "group_errors",
            {"project_name": "landingpage", "source_key": "backend"},
        )

    assert "Unknown source key 'backend'" in str(error_info.value)
    assert "Call list_projects" in str(error_info.value)
    assert error_info.value.tool_name == "group_errors"


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
