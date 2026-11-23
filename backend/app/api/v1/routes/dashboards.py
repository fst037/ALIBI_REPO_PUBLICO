"""`dashboards` domain routes: named boards, and the pool of pinned charts.

All endpoints require a bearer token and operate on the caller's own boards and
charts (owner scoping in the repository). Backed by
`app/services/dashboards.py`.

The one endpoint worth reading twice is
`GET /dashboards/{id}/charts/{chartId}/data`: it re-runs the chart's stored SQL
against the folder's data *as it is now*. One request per chart, not one for
the board — so a card whose query has gone stale fails alone, and no single
request has to fit an arbitrary number of queries inside API Gateway's 29s.
"""

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from app.api.v1.deps import require_user
from app.core.errors import error_response
from app.schemas.auth import User
from app.schemas.dashboards import (
    AddDashboardChartRequest,
    CreateDashboardRequest,
    Dashboard,
    DashboardChart,
    DashboardChartData,
    DashboardDetail,
    SavedChart,
    SaveChartRequest,
    UpdateDashboardRequest,
    UpdateLayoutRequest,
)
from app.services import dashboards as store

router = APIRouter(tags=["dashboards"])


@router.get("/dashboards", response_model=list[Dashboard], summary="The caller's dashboards.")
def list_dashboards(user: User = Depends(require_user)) -> list[Dashboard]:
    return store.list_dashboards(user.id)


@router.post(
    "/dashboards",
    response_model=Dashboard,
    status_code=201,
    summary="Create a dashboard over a folder.",
)
def create_dashboard(
    request: CreateDashboardRequest, user: User = Depends(require_user)
) -> Dashboard:
    return store.create_dashboard(user.id, request.name, request.folderId)


@router.get(
    "/dashboards/{dashboard_id}",
    response_model=DashboardDetail,
    summary="A dashboard with its charts (specs only — no data).",
)
def get_dashboard(
    dashboard_id: str, user: User = Depends(require_user)
) -> DashboardDetail | JSONResponse:
    dashboard = store.get_dashboard(user.id, dashboard_id)
    if dashboard is None:
        return error_response(404, "not_found", "No se encontró el dashboard.")
    return dashboard


@router.patch(
    "/dashboards/{dashboard_id}", response_model=Dashboard, summary="Rename a dashboard."
)
def update_dashboard(
    dashboard_id: str, request: UpdateDashboardRequest, user: User = Depends(require_user)
) -> Dashboard | JSONResponse:
    dashboard = store.rename_dashboard(user.id, dashboard_id, request.name)
    if dashboard is None:
        return error_response(404, "not_found", "No se encontró el dashboard.")
    return dashboard


@router.delete(
    "/dashboards/{dashboard_id}",
    status_code=204,
    response_model=None,
    summary="Delete a dashboard and its charts.",
)
def delete_dashboard(
    dashboard_id: str, user: User = Depends(require_user)
) -> Response | JSONResponse:
    if not store.delete_dashboard(user.id, dashboard_id):
        return error_response(404, "not_found", "No se encontró el dashboard.")
    return Response(status_code=204)


@router.get(
    "/dashboards/{dashboard_id}/available-charts",
    response_model=list[SavedChart],
    summary="Charts pinned from conversations over this dashboard's folder.",
)
def available_charts(dashboard_id: str, user: User = Depends(require_user)) -> list[SavedChart]:
    return store.available_charts(user.id, dashboard_id)


@router.post(
    "/dashboards/{dashboard_id}/charts",
    response_model=DashboardChart,
    status_code=201,
    summary="Add a chart to the dashboard, from a pin or from an answer.",
)
def add_chart(
    dashboard_id: str, request: AddDashboardChartRequest, user: User = Depends(require_user)
) -> DashboardChart:
    return store.add_chart(user.id, dashboard_id, request)


@router.delete(
    "/dashboards/{dashboard_id}/charts/{chart_id}",
    status_code=204,
    response_model=None,
    summary="Remove a chart from the dashboard.",
)
def remove_chart(
    dashboard_id: str, chart_id: str, user: User = Depends(require_user)
) -> Response | JSONResponse:
    if not store.remove_chart(user.id, dashboard_id, chart_id):
        return error_response(404, "not_found", "No se encontró el gráfico en este dashboard.")
    return Response(status_code=204)


@router.put(
    "/dashboards/{dashboard_id}/layout",
    response_model=DashboardDetail,
    summary="Persist the grid arrangement after a drag or resize.",
)
def set_layout(
    dashboard_id: str, request: UpdateLayoutRequest, user: User = Depends(require_user)
) -> DashboardDetail | JSONResponse:
    dashboard = store.set_layout(user.id, dashboard_id, request.layout)
    if dashboard is None:
        return error_response(404, "not_found", "No se encontró el dashboard.")
    return dashboard


@router.get(
    "/dashboards/{dashboard_id}/charts/{chart_id}/data",
    response_model=DashboardChartData,
    summary="Re-run one chart's query against the folder's current data.",
)
def chart_data(
    dashboard_id: str, chart_id: str, user: User = Depends(require_user)
) -> DashboardChartData:
    """Always 200 for a chart that exists.

    A query that no longer runs comes back as `status: "failed"` with a message
    for the card, not as an error status: the board is many independent
    statements about one folder, and one going stale is not a failure of the
    request. A missing chart is still a 404.
    """
    return store.chart_data(user.id, dashboard_id, chart_id)


@router.get(
    "/datasets/{dataset_id}/charts",
    response_model=list[SavedChart],
    summary="Saved charts for the dataset.",
)
def list_saved_charts(dataset_id: str, user: User = Depends(require_user)) -> list[SavedChart]:
    return store.list_by_dataset(user.id, dataset_id)


@router.post(
    "/datasets/{dataset_id}/charts",
    response_model=SavedChart,
    status_code=201,
    summary="Pin a chart to the dashboard.",
)
def save_chart(
    dataset_id: str, request: SaveChartRequest, user: User = Depends(require_user)
) -> SavedChart:
    return store.save_chart(
        user.id, dataset_id, request.messageId, request.chartType, request.findingIndex
    )


@router.get("/charts/{chart_id}", response_model=SavedChart, summary="Saved chart details.")
def get_saved_chart(chart_id: str, user: User = Depends(require_user)) -> SavedChart | JSONResponse:
    chart = store.get_saved_chart(user.id, chart_id)
    if chart is None:
        return error_response(404, "not_found", "No se encontró el gráfico guardado.")
    return chart


@router.delete(
    "/charts/{chart_id}",
    status_code=204,
    response_model=None,
    summary="Remove a chart from the dashboard.",
)
def remove_saved_chart(chart_id: str, user: User = Depends(require_user)) -> Response | JSONResponse:
    if not store.remove_saved_chart(user.id, chart_id):
        return error_response(404, "not_found", "No se encontró el gráfico guardado.")
    return Response(status_code=204)
