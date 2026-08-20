"""Tests for Grafana annotations client."""

import pytest
import requests

from sungrow_battery_balancer.grafana import GrafanaAnnotationError, GrafanaClient


@pytest.fixture
def grafana_client():
    return GrafanaClient(
        url="https://grafana.example.com",
        token="test_token",
        dashboard_uid="l1PwaTigk",
        panel_id=2,
    )


class TestGrafanaClient:
    def test_create_annotation_success(self, grafana_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {"id": 42, "message": "Annotation added"}

        mocker.patch.object(grafana_client.session, "post", return_value=mock_response)

        annotation_id = grafana_client.create_annotation(
            text="Max charging power set to 1.0 kW",
            tags=["battery-balancer", "custom-tag"],
            timestamp_ms=1700000000000,
        )

        assert annotation_id == 42
        grafana_client.session.post.assert_called_once_with(
            "https://grafana.example.com/api/annotations",
            json={
                "dashboardUID": "l1PwaTigk",
                "panelId": 2,
                "time": 1700000000000,
                "text": "Max charging power set to 1.0 kW",
                "tags": ["battery-balancer", "custom-tag"],
            },
            headers={
                "Authorization": "Bearer test_token",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=10.0,
            verify=True,
        )

    def test_dry_run_skips_http_post(self, grafana_client, mocker):
        mock_post = mocker.patch.object(grafana_client.session, "post")
        result = grafana_client.create_annotation(
            text="Simulated annotation",
            dry_run=True,
        )
        assert result is None
        mock_post.assert_not_called()

    def test_unauthorized_token(self, grafana_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 401
        mock_response.ok = False

        mocker.patch.object(grafana_client.session, "post", return_value=mock_response)

        with pytest.raises(
            GrafanaAnnotationError, match="Unauthorized: Check service account token"
        ):
            grafana_client.create_annotation("Test")

    def test_server_error(self, grafana_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 500
        mock_response.ok = False
        mock_response.text = "Internal error"

        mocker.patch.object(grafana_client.session, "post", return_value=mock_response)

        with pytest.raises(GrafanaAnnotationError, match="HTTP 500"):
            grafana_client.create_annotation("Test")

    def test_connection_error(self, grafana_client, mocker):
        mocker.patch.object(
            grafana_client.session,
            "post",
            side_effect=requests.exceptions.ConnectTimeout("Grafana timeout"),
        )

        with pytest.raises(GrafanaAnnotationError, match="Connection failed"):
            grafana_client.create_annotation("Test")

    def test_create_annotation_json_decode_error(self, grafana_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mocker.patch.object(grafana_client.session, "post", return_value=mock_response)

        res = grafana_client.create_annotation("Test")
        assert res is None

    def test_close(self, grafana_client, mocker):
        mock_close = mocker.patch.object(grafana_client.session, "close")
        grafana_client.close()
        mock_close.assert_called_once()
