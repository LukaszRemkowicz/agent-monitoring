import json
from typing import Any

import httpx
import pytest

from exceptions import McpClientError
from mcp import McpWorkflowClient
from schemas import (
    CollectLogsArtifact,
    LogWorkspace,
    McpToolName,
    ProjectManifestSummary,
    StructuredContent,
)
from tests.conftest import build_collect_logs_artifact_payload


def build_start_log_collection_response(session_id: str = "workflow-session") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "result": {
                "structuredContent": {
                    "action": "start_log_collection",
                    "status": "started",
                    "workspace": LogWorkspace.WORKFLOW,
                    "session_id": session_id,
                    "next_step_tips": [],
                }
            }
        },
    )


def build_log_collection_status_response(
    *,
    session_id: str = "workflow-session",
    status: str = "completed",
    result: dict[str, object] | None = None,
) -> httpx.Response:
    task: dict[str, object] = {
        "project_name": "demo-shop",
        "status": status,
        "created_at": "2026-05-20T00:00:00Z",
    }
    if result is not None:
        task["result"] = result
    return httpx.Response(
        200,
        json={
            "result": {
                "structuredContent": {
                    "action": "get_log_collection_status",
                    "task_type": "log_collection",
                    "workspace": LogWorkspace.WORKFLOW,
                    "session_id": session_id,
                    "task_count": 1,
                    "created_at": "2026-05-20T00:00:00Z",
                    "tasks": [task],
                }
            }
        },
    )


def test_mcp_workflow_client_defaults_to_longer_timeout() -> None:
    client = McpWorkflowClient(base_url="http://mcp.local/mcp", workflow_jwt="workflow-token")

    assert client.timeout_seconds == 90.0


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
                    "workflow_name": McpToolName.ANALYZE_DAILY_LOG_BUNDLE,
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

    structured_content: StructuredContent = await client.call_tool(
        McpToolName.ANALYZE_DAILY_LOG_BUNDLE
    )

    assert structured_content.workflow_name == McpToolName.ANALYZE_DAILY_LOG_BUNDLE
    assert structured_content.mandatory_skills[0].name == "project_context"
    assert len(requests) == 1
    request_payload = httpx.Request(
        "POST",
        "http://mcp.local/mcp",
        content=requests[0].content,
    ).read()
    assert b'"method":"tools/call"' in request_payload
    assert f'"name":"{McpToolName.ANALYZE_DAILY_LOG_BUNDLE}"'.encode() in request_payload


@pytest.mark.asyncio
async def test_mcp_workflow_client_fetches_keycloak_token_for_tool_calls() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/protocol/openid-connect/token"):
            form_body = request.content.decode()
            assert "grant_type=client_credentials" in form_body
            assert "client_id=workflow-agent" in form_body
            assert "client_secret=workflow-secret" in form_body
            return httpx.Response(
                200,
                json={"access_token": "keycloak-workflow-token", "expires_in": 3600},
            )

        assert request.headers["authorization"] == "Bearer keycloak-workflow-token"
        return httpx.Response(
            200,
            json={
                "result": {
                    "structuredContent": {
                        "name": "workflow-mcp",
                        "status": "ok",
                    }
                }
            },
        )

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        keycloak_url="https://auth.example.com/realms/mcp",
        keycloak_client_id="workflow-agent",
        keycloak_client_secret="workflow-secret",
        transport=httpx.MockTransport(handler),
    )

    status = await client.get_service_status()

    assert status.status == "ok"
    assert [request.url.path for request in requests] == [
        "/realms/mcp/protocol/openid-connect/token",
        "/mcp",
    ]


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
                        "workflow_name": McpToolName.ANALYZE_DAILY_LOG_BUNDLE,
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

    assert workflow.workflow_name == McpToolName.ANALYZE_DAILY_LOG_BUNDLE
    assert workflow.mandatory_skills[0].name == "project_context"
    assert f'"name":"{McpToolName.ANALYZE_DAILY_LOG_BUNDLE}"' in tool_names[0]


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
                        "workflow_name": McpToolName.ANALYZE_SITEMAP_BUNDLE,
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

    assert workflow.workflow_name == McpToolName.ANALYZE_SITEMAP_BUNDLE
    assert workflow.prompt == "Sitemap Summary Instructions"
    assert f'"name":"{McpToolName.ANALYZE_SITEMAP_BUNDLE}"' in tool_names[0]


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
                        "name": "workflow-mcp",
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
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(request.content)
        requests.append(request_payload)
        tool_name = request_payload["params"]["name"]
        if tool_name == McpToolName.START_LOG_COLLECTION:
            return build_start_log_collection_response()
        if tool_name == McpToolName.GET_LOG_COLLECTION_STATUS:
            return build_log_collection_status_response(
                result=build_collect_logs_artifact_payload(session_id="workflow-session")
            )
        raise AssertionError(f"unexpected tool call: {tool_name}")

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
        collect_logs_poll_interval_seconds=0.0,
    )

    artifact: CollectLogsArtifact = await client.collect_logs(
        since="2026-05-19T00:00:00Z",
        until="2026-05-20T00:00:00Z",
    )

    assert artifact.action == McpToolName.COLLECT_LOGS
    assert artifact.session_id == "workflow-session"
    assert artifact.projects[0].snapshot_dir == "workflow/demo-shop/latest"
    assert artifact.projects[0].sources[0].source_key == "backend"
    assert requests[0]["method"] == "tools/call"
    assert requests[0]["params"] == {
        "name": McpToolName.START_LOG_COLLECTION,
        "arguments": {
            "since": "2026-05-19T00:00:00Z",
            "until": "2026-05-20T00:00:00Z",
        },
    }
    assert requests[1]["params"] == {
        "name": McpToolName.GET_LOG_COLLECTION_STATUS,
        "arguments": {"session_id": "workflow-session"},
    }


@pytest.mark.asyncio
async def test_mcp_workflow_client_collect_logs_accepts_provenance_diagnostics() -> None:
    payload = build_collect_logs_artifact_payload()
    payload["session_id"] = "workflow-session"
    payload["projects"][0]["provenance_diagnostics"] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(request.content)
        tool_name = request_payload["params"]["name"]
        if tool_name == McpToolName.START_LOG_COLLECTION:
            return build_start_log_collection_response()
        if tool_name == McpToolName.GET_LOG_COLLECTION_STATUS:
            return build_log_collection_status_response(result=payload)
        raise AssertionError(f"unexpected tool call: {tool_name}")

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
        collect_logs_poll_interval_seconds=0.0,
    )

    artifact: CollectLogsArtifact = await client.collect_logs(
        since="2026-05-19T00:00:00Z",
        until="2026-05-20T00:00:00Z",
    )

    assert artifact.projects[0].provenance_diagnostics == []


@pytest.mark.asyncio
async def test_mcp_workflow_client_collect_logs_polls_async_collection_until_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr("mcp.asyncio.sleep", fake_sleep)
    monotonic_values = [0.0, 0.0, 30.0, 60.0]

    def fake_monotonic() -> float:
        if monotonic_values:
            return monotonic_values.pop(0)
        return 60.0

    monkeypatch.setattr("mcp.time.monotonic", fake_monotonic)

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(request.content)
        requests.append(request_payload)
        tool_name = request_payload["params"]["name"]
        if tool_name == McpToolName.START_LOG_COLLECTION:
            return build_start_log_collection_response()
        if tool_name == McpToolName.GET_LOG_COLLECTION_STATUS:
            poll_number = sum(
                1
                for item in requests
                if item["params"]["name"] == McpToolName.GET_LOG_COLLECTION_STATUS
            )
            task_status = "running" if poll_number == 1 else "completed"
            task_result = None
            if task_status == "completed":
                task_result = build_collect_logs_artifact_payload(session_id="workflow-session")
            return build_log_collection_status_response(
                status=task_status,
                result=task_result,
            )
        raise AssertionError(f"unexpected tool call: {tool_name}")

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
    )

    artifact: CollectLogsArtifact = await client.collect_logs(
        since="2026-05-19T00:00:00Z",
        until="2026-05-20T00:00:00Z",
    )

    assert artifact.session_id == "workflow-session"
    assert sleep_calls == [30.0]
    assert [request["params"]["name"] for request in requests] == [
        McpToolName.START_LOG_COLLECTION,
        McpToolName.GET_LOG_COLLECTION_STATUS,
        McpToolName.GET_LOG_COLLECTION_STATUS,
    ]
    assert requests[0]["params"] == {
        "name": McpToolName.START_LOG_COLLECTION,
        "arguments": {
            "since": "2026-05-19T00:00:00Z",
            "until": "2026-05-20T00:00:00Z",
        },
    }


@pytest.mark.asyncio
async def test_mcp_workflow_client_collect_logs_times_out_when_async_collection_stays_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr("mcp.asyncio.sleep", fake_sleep)
    monotonic_values = [0.0, 0.0, 30.0, 60.0]

    def fake_monotonic() -> float:
        if monotonic_values:
            return monotonic_values.pop(0)
        return 60.0

    monkeypatch.setattr("mcp.time.monotonic", fake_monotonic)

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(request.content)
        tool_name = request_payload["params"]["name"]
        if tool_name == McpToolName.START_LOG_COLLECTION:
            return build_start_log_collection_response()
        if tool_name == McpToolName.GET_LOG_COLLECTION_STATUS:
            return build_log_collection_status_response(status="running")
        raise AssertionError(f"unexpected tool call: {tool_name}")

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
        collect_logs_poll_timeout_seconds=60.0,
        collect_logs_poll_interval_seconds=30.0,
    )

    with pytest.raises(McpClientError) as error_info:
        await client.collect_logs(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
        )

    assert "timed out after 60 seconds" in str(error_info.value)
    assert sleep_calls == [30.0, 30.0]


@pytest.mark.asyncio
async def test_mcp_workflow_client_collect_logs_raises_tool_error_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(request.content)
        assert request_payload["params"]["name"] == McpToolName.START_LOG_COLLECTION
        return httpx.Response(
            200,
            json={
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Unknown project 'demo-shop'.",
                        }
                    ],
                    "structuredContent": {
                        "status": "error",
                        "error_code": "unknown_project",
                        "message": "Unknown project 'demo-shop'.",
                        "retry_tips": ["Call list_projects."],
                        "details": {"requested_project_names": ["demo-shop"]},
                    },
                    "isError": True,
                }
            },
        )

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(McpClientError) as error_info:
        await client.collect_logs(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
        )

    assert "Unknown project 'demo-shop'" in str(error_info.value)
    assert "Call list_projects" in str(error_info.value)
    assert error_info.value.tool_name == McpToolName.START_LOG_COLLECTION


@pytest.mark.asyncio
async def test_mcp_workflow_client_collect_logs_validation_error_lists_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(request.content)
        tool_name = request_payload["params"]["name"]
        if tool_name == McpToolName.START_LOG_COLLECTION:
            return build_start_log_collection_response()
        if tool_name == McpToolName.GET_LOG_COLLECTION_STATUS:
            return build_log_collection_status_response(
                result={
                    "action": McpToolName.COLLECT_LOGS,
                    "workspace": LogWorkspace.WORKFLOW,
                    "projects": [
                        {
                            "project_name": "demo-shop",
                            "workspace": LogWorkspace.WORKFLOW,
                            "snapshot_dir": "workflow/demo-shop/latest",
                            "collected_at": "2026-05-20T00:01:00Z",
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected tool call: {tool_name}")

    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(handler),
        collect_logs_poll_interval_seconds=0.0,
    )

    with pytest.raises(McpClientError) as error_info:
        await client.collect_logs(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
        )

    message = str(error_info.value)
    assert (
        "MCP get_log_collection_status result did not match expected collect_logs shape" in message
    )
    assert "projects.0.requested_project_name" in message


@pytest.mark.asyncio
async def test_mcp_workflow_client_list_projects_uses_discovery_tool() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "result": {
                    "structuredContent": {
                        "result": [
                            {
                                "project_name": "demo-shop",
                                "project_summary": "Demo shop project.",
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

    assert [project.project_name for project in projects] == ["demo-shop", "shop"]
    assert projects[0].source_keys == ["backend", "nginx"]
    assert requests[0]["params"] == {
        "name": McpToolName.LIST_PROJECTS,
        "arguments": {},
    }


@pytest.mark.asyncio
async def test_mcp_workflow_client_list_projects_returns_empty_list() -> None:
    requests: list[dict[str, Any]] = []

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
        "name": McpToolName.LIST_PROJECTS,
        "arguments": {},
    }


@pytest.mark.asyncio
async def test_mcp_workflow_client_reads_workflow_skill_resource() -> None:
    requests: list[dict[str, Any]] = []

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
    assert requests[0]["method"] == McpToolName.READ_RESOURCE
    assert requests[0]["params"] == {"uri": "skill://workflow/project_context"}


@pytest.mark.asyncio
async def test_mcp_workflow_client_calls_deterministic_tool() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "result": {
                    "structuredContent": {
                        "action": McpToolName.GROUP_ERRORS,
                        "project_name": "demo-shop",
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
        McpToolName.GROUP_ERRORS,
        {"project_name": "demo-shop"},
    )

    assert structured_content["action"] == McpToolName.GROUP_ERRORS
    assert structured_content["project_name"] == "demo-shop"
    assert requests[0]["params"] == {
        "name": McpToolName.GROUP_ERRORS,
        "arguments": {"project_name": "demo-shop"},
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
            McpToolName.GROUP_ERRORS,
            {"project_name": "demo-shop", "source_key": "backend"},
        )

    assert "Unknown source key 'backend'" in str(error_info.value)
    assert "Call list_projects" in str(error_info.value)
    assert error_info.value.tool_name == McpToolName.GROUP_ERRORS


@pytest.mark.asyncio
async def test_mcp_workflow_client_requires_workflow_jwt() -> None:
    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )

    with pytest.raises(RuntimeError, match="MCP_WORKFLOW_JWT or complete MCP Keycloak"):
        await client.call_tool(McpToolName.ANALYZE_DAILY_LOG_BUNDLE)


@pytest.mark.asyncio
async def test_mcp_workflow_client_rejects_partial_keycloak_config() -> None:
    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        keycloak_url="https://auth.example.com/realms/mcp",
        keycloak_client_id="workflow-agent",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )

    with pytest.raises(RuntimeError, match="MCP Keycloak auth is partially configured"):
        await client.call_tool(McpToolName.ANALYZE_DAILY_LOG_BUNDLE)


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
        await client.call_tool(McpToolName.ANALYZE_DAILY_LOG_BUNDLE)


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
        await client.call_tool(McpToolName.ANALYZE_DAILY_LOG_BUNDLE)


@pytest.mark.asyncio
async def test_mcp_workflow_client_raises_for_non_object_response() -> None:
    client = McpWorkflowClient(
        base_url="http://mcp.local/mcp",
        workflow_jwt="workflow-token",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
    )

    with pytest.raises(RuntimeError, match="must be a JSON object"):
        await client.call_tool(McpToolName.ANALYZE_DAILY_LOG_BUNDLE)
