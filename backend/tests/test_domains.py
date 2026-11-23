from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _auth_headers() -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"email": "ana@acme.com", "password": "pw"})
    return {"Authorization": f"Bearer {login.json()['accessToken']}"}


AUTH = _auth_headers()


# ---- plans (public) ----


def test_list_plans_public() -> None:
    res = client.get("/api/v1/plans")
    assert res.status_code == 200
    plans = {p["id"]: p for p in res.json()}
    # The business model: two plans charged in ARS, overage on Pro instead of a
    # third plan, every price a USD reference value at the official rate.
    assert set(plans) == {"free", "pro"}
    assert plans["pro"]["currency"] == "ARS"
    assert (plans["pro"]["priceMonthlyUsd"], plans["pro"]["priceAnnualUsd"]) == (20, 16)
    assert plans["pro"]["overagePricePerQueryUsd"] == 0.08
    assert plans["pro"]["usdArsRate"] == 1550
    assert (plans["pro"]["priceMonthly"], plans["pro"]["priceAnnual"]) == (31000, 24800)
    assert plans["pro"]["overagePricePerQuery"] == 124
    assert plans["free"]["overagePricePerQuery"] is None
    assert plans["free"]["limits"]["maxFileMb"] == 5
    assert plans["free"]["limits"]["historyDays"] == 7


def test_get_plan() -> None:
    res = client.get("/api/v1/plans/pro")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Pro"
    assert body["limits"]["datasets"] is None  # null must be emitted


# ---- account ----


def test_account_requires_auth() -> None:
    assert client.get("/api/v1/account").status_code == 401


def test_get_and_update_account() -> None:
    assert client.get("/api/v1/account", headers=AUTH).status_code == 200
    res = client.patch("/api/v1/account", headers=AUTH, json={"workspace": "New Org"})
    assert res.status_code == 200
    assert res.json()["workspace"] == "New Org"


def test_update_account_empty_422() -> None:
    assert client.patch("/api/v1/account", headers=AUTH, json={}).status_code == 422


# ---- notifications ----


def test_notifications_flow() -> None:
    assert client.get("/api/v1/notifications/unread-count", headers=AUTH).json()["count"] == 2
    listed = client.get("/api/v1/notifications", headers=AUTH)
    assert listed.status_code == 200
    # n_4 has no action → omitted from the wire shape.
    n4 = next(n for n in listed.json() if n["id"] == "n_4")
    assert "action" not in n4

    assert client.post("/api/v1/notifications/n_1/read", headers=AUTH).status_code == 200
    assert client.get("/api/v1/notifications/unread-count", headers=AUTH).json()["count"] == 1
    assert client.post("/api/v1/notifications/nope/read", headers=AUTH).status_code == 404

    client.post("/api/v1/notifications/read-all", headers=AUTH)
    assert client.get("/api/v1/notifications/unread-count", headers=AUTH).json()["count"] == 0


# ---- conversations (chat) ----


def test_list_and_get_conversation() -> None:
    listed = client.get("/api/v1/conversations", headers=AUTH)
    assert listed.status_code == 200
    assert any(c["id"] == "conv_sales_region" for c in listed.json())

    detail = client.get("/api/v1/conversations/conv_sales_region", headers=AUTH)
    assert detail.status_code == 200
    # A stored answer is a sentence, its SQL, and its table. The chart fields the
    # fixture was written with are dropped on read — `QueryResult` no longer
    # declares them, so old transcripts load without them rather than failing.
    result = detail.json()["messages"][1]["result"]
    assert result["naturalResponse"]
    assert result["sql"]
    assert "autoChartType" not in result

    assert client.get("/api/v1/conversations/nope", headers=AUTH).status_code == 404


def test_suggestions() -> None:
    res = client.get("/api/v1/conversations/suggestions", headers=AUTH)
    assert res.status_code == 200
    assert res.json()[0]["icon"] == "trend"


def test_a_seeded_conversation_cannot_answer_from_nothing() -> None:
    """The prepare/execute cycle over a fixture conversation reports, not fakes.

    `conv_sales_region` names datasets with no materialised Parquet behind them.
    It used to answer anyway, from a canned `QueryResult` — the demo's numbers
    under whatever SQL had been prepared, indistinguishable from a real answer.
    The real cycle is exercised end to end in `test_chat_execution.py`, over data
    that actually exists.
    """
    prepared = client.post(
        "/api/v1/conversations/conv_sales_region/messages/prepare",
        headers=AUTH,
        json={"question": "Sales by region"},
    )
    # 503: no Text-to-SQL model configured in the suite, so no SQL can be
    # generated. 409 if the conversation has nothing queryable. Never 200 with
    # an invented query.
    assert prepared.status_code in (409, 503)


def test_a_draft_is_one_shot(client, auth_headers, conversation_over) -> None:
    """Confirming twice must not run the query twice."""
    from tests.test_chat_execution import _dataset

    dataset = _dataset(client, auth_headers)
    conversation_id = conversation_over(auth_headers, [dataset["id"]])
    prepared = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/prepare",
        json={"sql": f"SELECT ciudad FROM {dataset['id']}"},
        headers=auth_headers,
    )
    assert prepared.status_code == 200
    draft_id = prepared.json()["draftId"]

    first = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"draftId": draft_id},
        headers=auth_headers,
    )
    assert first.status_code == 201
    assert first.json()["message"]["role"] == "assistant"

    second = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"draftId": draft_id},
        headers=auth_headers,
    )
    assert second.status_code == 404


def test_prepare_guardrail_blocks_writes() -> None:
    res = client.post(
        "/api/v1/conversations/conv_sales_region/messages/prepare",
        headers=AUTH,
        json={"question": "delete all sales rows"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "blocked"


def test_prepare_sql_validation() -> None:
    ok = client.post(
        "/api/v1/conversations/conv_sales_region/messages/prepare",
        headers=AUTH,
        json={"sql": "SELECT region FROM sales"},
    )
    assert ok.status_code == 200
    bad = client.post(
        "/api/v1/conversations/conv_sales_region/messages/prepare",
        headers=AUTH,
        json={"sql": "DROP TABLE sales"},
    )
    assert bad.status_code == 422


def test_prepare_requires_exactly_one_input() -> None:
    res = client.post(
        "/api/v1/conversations/conv_sales_region/messages/prepare",
        headers=AUTH,
        json={"question": "x", "sql": "SELECT 1"},
    )
    assert res.status_code == 422


# ---- library ----


def test_history_and_saved_queries() -> None:
    assert client.get("/api/v1/history", headers=AUTH).status_code == 200

    filtered = client.get("/api/v1/saved-queries", headers=AUTH, params={"q": "margen"})
    assert filtered.status_code == 200
    assert all("margen" in q["question"].lower() for q in filtered.json())

    saved = client.post(
        "/api/v1/saved-queries",
        headers=AUTH,
        json={"question": "New query", "datasetId": "sales", "chartType": "bar"},
    )
    assert saved.status_code == 201
    assert saved.json()["datasetName"] == "sales_2024.csv"
    sq_id = saved.json()["id"]

    assert client.delete(f"/api/v1/saved-queries/{sq_id}", headers=AUTH).status_code == 204
    assert client.delete(f"/api/v1/saved-queries/{sq_id}", headers=AUTH).status_code == 404


# ---- dashboards ----


def test_saved_charts_flow() -> None:
    listed = client.get("/api/v1/datasets/sales/charts", headers=AUTH)
    assert listed.status_code == 200
    assert all(c["datasetId"] == "sales" for c in listed.json())

    assert client.get("/api/v1/datasets/nope/charts", headers=AUTH).status_code == 404

    saved = client.post(
        "/api/v1/datasets/sales/charts",
        headers=AUTH,
        json={"messageId": "m_2", "chartType": "pie"},
    )
    assert saved.status_code == 201
    chart_id = saved.json()["id"]
    assert saved.json()["chartType"] == "pie"

    assert client.get(f"/api/v1/charts/{chart_id}", headers=AUTH).status_code == 200
    assert client.delete(f"/api/v1/charts/{chart_id}", headers=AUTH).status_code == 204
    assert client.get(f"/api/v1/charts/{chart_id}", headers=AUTH).status_code == 404


def test_save_chart_unknown_message_404() -> None:
    res = client.post(
        "/api/v1/datasets/sales/charts",
        headers=AUTH,
        json={"messageId": "m_nope", "chartType": "bar"},
    )
    assert res.status_code == 404


# ---- billing + exports (state is reset per test by conftest) ----


def _switch_to(plan_id: str) -> None:
    res = client.post(
        "/api/v1/subscription/upgrade", headers=AUTH, json={"planId": plan_id, "cycle": "monthly"}
    )
    assert res.status_code == 200, res.text


def test_peso_prices_follow_the_official_rate(monkeypatch) -> None:
    """The peso price is the USD reference value at the configured rate, on
    every read: changing the rate reprices the plans with no migration."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "usd_ars_rate", 1600.0)
    monkeypatch.setattr(get_settings(), "usd_ars_rate_as_of", "2026-12-01")
    pro = client.get("/api/v1/plans/pro").json()
    assert (pro["priceMonthly"], pro["priceAnnual"], pro["overagePricePerQuery"]) == (32000, 25600, 128)
    assert pro["usdArsRateAsOf"] == "2026-12-01"
    # The overage is billed at the price of the day too.
    sub = client.get("/api/v1/subscription", headers=AUTH).json()
    assert sub["overage"]["pricePerQueryArs"] == 128


def test_subscription_seed_is_pro() -> None:
    res = client.get("/api/v1/subscription", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["planId"] == "pro"
    assert body["paymentMethod"] is None  # null must be emitted
    assert body["overage"]["available"] is True
    assert body["overage"]["enabled"] is False
    assert body["overage"]["pricePerQueryArs"] == 124


def test_business_plan_is_gone() -> None:
    assert client.get("/api/v1/plans/business").status_code == 422


def test_export_csv_allowed_on_free() -> None:
    _switch_to("free")
    res = client.post(
        "/api/v1/exports", headers=AUTH, json={"source": {"messageId": "m_2"}, "format": "csv"}
    )
    assert res.status_code == 201
    assert res.json()["format"] == "csv"
    assert res.json()["downloadUrl"].endswith(".csv")


def test_export_png_blocked_on_free() -> None:
    _switch_to("free")
    res = client.post(
        "/api/v1/exports",
        headers=AUTH,
        json={"source": {"messageId": "m_2"}, "format": "png", "chartType": "bar"},
    )
    assert res.status_code == 402
    assert res.json()["error"]["code"] == "plan_upgrade_required"


def test_export_unknown_source_404() -> None:
    res = client.post(
        "/api/v1/exports", headers=AUTH, json={"source": {"messageId": "nope"}, "format": "csv"}
    )
    assert res.status_code == 404


def test_ledger_and_export() -> None:
    ledger = client.get("/api/v1/billing/ledger", headers=AUTH)
    assert ledger.status_code == 200
    assert ledger.json()["totalUsd"] > 0

    csv_export = client.get("/api/v1/billing/ledger/export", headers=AUTH)
    assert csv_export.status_code == 200
    assert csv_export.headers["content-type"].startswith("text/csv")


def test_upgrade_then_png_export() -> None:
    _switch_to("free")
    # Pro cannot be bought through the plan-change endpoint (see test_checkout).
    refused = client.post(
        "/api/v1/subscription/upgrade", headers=AUTH, json={"planId": "pro", "cycle": "annual"}
    )
    assert refused.status_code == 402
    assert refused.json()["error"]["code"] == "payment_required"

    from app.schemas.billing import UpgradeRequest
    from app.services import billing_memory

    billing_memory.upgrade("u", UpgradeRequest(planId="pro", cycle="annual"))
    upgraded = client.get("/api/v1/subscription", headers=AUTH)
    assert upgraded.json()["planId"] == "pro"

    # PNG now allowed, but chartType is required.
    missing = client.post(
        "/api/v1/exports", headers=AUTH, json={"source": {"messageId": "m_2"}, "format": "png"}
    )
    assert missing.status_code == 422

    ok = client.post(
        "/api/v1/exports",
        headers=AUTH,
        json={"source": {"messageId": "m_2"}, "format": "png", "chartType": "bar"},
    )
    assert ok.status_code == 201
    assert ok.json()["format"] == "png"


def test_payment_method() -> None:
    pm = client.put(
        "/api/v1/billing/payment-method", headers=AUTH, json={"paymentToken": "tok_123"}
    )
    assert pm.status_code == 200
    assert pm.json()["last4"] == "4242"


def test_enterprise_lead_is_gone() -> None:
    lead = client.post(
        "/api/v1/sales/enterprise-lead",
        headers=AUTH,
        json={"name": "Ana", "email": "ana@acme.com", "organizationSize": "50-200"},
    )
    assert lead.status_code in (404, 405)
