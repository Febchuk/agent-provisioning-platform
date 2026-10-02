"""API-contract tests for the agents/versions/deploy/templates/files endpoints
(specs/03-chat-and-deploy.md CD-1, CD-2, CD-3, CD-10; specs/07 §1 "API
contract" row: httpx AsyncClient, no network/Docker).
"""
import pytest

pytestmark = pytest.mark.asyncio


async def test_get_templates_lists_all_three(app_client):
    resp = await app_client.get("/templates")
    assert resp.status_code == 200
    ids = {t["id"] for t in resp.json()}
    assert ids == {"blank", "data-analyst", "repo-helper"}


# ---------------------------------------------------------------------------
# AC-CD-a
# ---------------------------------------------------------------------------
async def test_create_agent_from_data_analyst_template(app_client):
    resp = await app_client.post(
        "/agents", json={"name": "Revenue Analyst", "description": "Analyzes orders", "template": "data-analyst"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["deployed_version_id"] is not None

    detail = await app_client.get(f"/agents/{body['id']}")
    assert detail.status_code == 200
    versions = detail.json()["versions"]
    assert len(versions) == 1
    v1 = versions[0]
    assert v1["number"] == 1
    assert v1["id"] == body["deployed_version_id"]
    assert set(v1["tools"]) == {"bash", "read_file", "write_file", "edit_file", "list_files"}
    file_names = {f["name"] for f in v1["files"]}
    assert "orders.csv" in file_names


async def test_create_agent_slug_is_url_safe_and_unique(app_client):
    r1 = await app_client.post("/agents", json={"name": "My Cool Agent!", "template": "blank"})
    r2 = await app_client.post("/agents", json={"name": "My Cool Agent!", "template": "blank"})
    assert r1.status_code == 201 and r2.status_code == 201

    a1 = await app_client.get(f"/agents/{r1.json()['id']}")
    a2 = await app_client.get(f"/agents/{r2.json()['id']}")
    slug1 = a1.json()["slug"]
    slug2 = a2.json()["slug"]
    assert slug1 != slug2
    assert slug1 == "my-cool-agent"
    assert slug2.startswith("my-cool-agent-")
    assert " " not in slug1 and "!" not in slug1


async def test_list_agents_shape_has_no_crash_with_zero_stats(app_client):
    created = await app_client.post("/agents", json={"name": "Blank One", "template": "blank"})
    assert created.status_code == 201

    resp = await app_client.get("/agents")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["deployed_version_number"] == 1
    assert row["chat_count_7d"] == 0
    assert row["new_feedback_count"] == 0
    assert row["latest_eval_score"] is None


# ---------------------------------------------------------------------------
# CD-2 / AC-CD-b (deploy half; conversation-pinning half is in test_api_chat.py)
# ---------------------------------------------------------------------------
async def test_create_version_copies_unspecified_fields_and_increments_number(app_client):
    created = await app_client.post(
        "/agents", json={"name": "Repo Bot", "template": "repo-helper", "description": "d"}
    )
    agent_id = created.json()["id"]

    resp = await app_client.post(
        f"/agents/{agent_id}/versions", json={"change_note": "tweak guidelines only"}
    )
    assert resp.status_code == 201
    v2 = resp.json()
    assert v2["number"] == 2
    assert v2["source"] == "manual"
    # system_prompt/tools not given -> copied from v1 (the repo-helper template).
    v1_detail = await app_client.get(f"/agents/{agent_id}")
    versions = {v["number"]: v for v in v1_detail.json()["versions"]}
    assert v2["system_prompt"] == versions[1]["system_prompt"]
    assert v2["tools"] == versions[1]["tools"]


async def test_create_version_with_explicit_system_prompt_overrides_parent(app_client):
    created = await app_client.post("/agents", json={"name": "Override Test", "template": "blank"})
    agent_id = created.json()["id"]

    resp = await app_client.post(
        f"/agents/{agent_id}/versions", json={"system_prompt": "New custom prompt."}
    )
    assert resp.status_code == 201
    assert resp.json()["system_prompt"] == "New custom prompt."


async def test_agents_get_detail_versions_newest_first(app_client):
    created = await app_client.post("/agents", json={"name": "Order Test", "template": "blank"})
    agent_id = created.json()["id"]
    await app_client.post(f"/agents/{agent_id}/versions", json={})
    await app_client.post(f"/agents/{agent_id}/versions", json={})

    detail = await app_client.get(f"/agents/{agent_id}")
    numbers = [v["number"] for v in detail.json()["versions"]]
    assert numbers == [3, 2, 1]


# ---------------------------------------------------------------------------
# CD-3
# ---------------------------------------------------------------------------
async def test_deploy_version_from_another_agent_returns_400(app_client):
    a1 = (await app_client.post("/agents", json={"name": "Agent One", "template": "blank"})).json()
    a2 = (await app_client.post("/agents", json={"name": "Agent Two", "template": "blank"})).json()

    # a2's v1 id used to deploy on a1 -> 400.
    a2_detail = (await app_client.get(f"/agents/{a2['id']}")).json()
    a2_v1_id = a2_detail["versions"][0]["id"]

    resp = await app_client.post(f"/agents/{a1['id']}/deploy", json={"version_id": a2_v1_id})
    assert resp.status_code == 400


async def test_deploy_valid_version_moves_pointer(app_client):
    created = (await app_client.post("/agents", json={"name": "Deploy Test", "template": "blank"})).json()
    agent_id = created["id"]
    v2 = (await app_client.post(f"/agents/{agent_id}/versions", json={"change_note": "v2"})).json()

    resp = await app_client.post(f"/agents/{agent_id}/deploy", json={"version_id": v2["id"]})
    assert resp.status_code == 200
    assert resp.json()["deployed_version_id"] == v2["id"]


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------
async def test_upload_file_is_added_to_next_version(app_client):
    created = (await app_client.post("/agents", json={"name": "File Test", "template": "blank"})).json()
    agent_id = created["id"]

    resp = await app_client.post(
        f"/agents/{agent_id}/files",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["file"]["name"] == "notes.txt"
    assert body["file"]["size"] == len(b"hello world")
    # The version created as part of the upload call already has the file.
    assert "notes.txt" in {f["name"] for f in body["version"]["files"]}

    # And the next version created afterwards (no explicit files) still has it.
    next_version = (await app_client.post(f"/agents/{agent_id}/versions", json={})).json()
    assert "notes.txt" in {f["name"] for f in next_version["files"]}


# ---------------------------------------------------------------------------
# AC-CD-e (share redaction) — share endpoints exercised fully in test_api_chat.py,
# but this covers the no-agent-found case defensively here too.
# ---------------------------------------------------------------------------
async def test_share_unknown_slug_404(app_client):
    resp = await app_client.get("/share/does-not-exist")
    assert resp.status_code == 404
