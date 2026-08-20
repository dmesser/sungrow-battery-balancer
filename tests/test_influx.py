"""Tests for InfluxDB client, query parsing, and error scenarios."""

import pytest
import requests

from sungrow_battery_balancer.influx import (
    InfluxClient,
    InfluxConnectionError,
    InfluxQueryError,
)


@pytest.fixture
def influx_client():
    return InfluxClient(
        url="https://influx.local:8086",
        user="test_user",
        password="test_password",
        db="pv_monitoring",
        query="SELECT MAX(last_battery_level) FROM sungather",
        verify_ssl=True,
    )


class TestInfluxClient:
    def test_fetch_battery_soc_success(self, influx_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "results": [
                {
                    "statement_id": 0,
                    "series": [
                        {
                            "name": "sungather",
                            "columns": ["time", "max"],
                            "values": [["2026-08-20T11:12:00Z", 87.5]],
                        }
                    ],
                }
            ]
        }

        mocker.patch.object(influx_client.session, "get", return_value=mock_response)

        soc = influx_client.fetch_battery_soc()
        assert soc == 87.5
        influx_client.session.get.assert_called_once_with(
            "https://influx.local:8086/query",
            params={
                "db": "pv_monitoring",
                "q": "SELECT MAX(last_battery_level) FROM sungather",
            },
            auth=("test_user", "test_password"),
            verify=True,
            timeout=10.0,
        )

    def test_unauthorized_error(self, influx_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 401
        mock_response.ok = False

        mocker.patch.object(influx_client.session, "get", return_value=mock_response)

        with pytest.raises(InfluxQueryError, match="Unauthorized: Check username and password"):
            influx_client.fetch_battery_soc()

    def test_http_server_error(self, influx_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 500
        mock_response.ok = False
        mock_response.text = "Internal Server Error"

        mocker.patch.object(influx_client.session, "get", return_value=mock_response)

        with pytest.raises(InfluxQueryError, match="HTTP 500"):
            influx_client.fetch_battery_soc()

    def test_connection_error(self, influx_client, mocker):
        mocker.patch.object(
            influx_client.session,
            "get",
            side_effect=requests.exceptions.ConnectTimeout("Connection timed out"),
        )

        with pytest.raises(InfluxConnectionError, match="Connection failed"):
            influx_client.fetch_battery_soc()

    def test_ssl_error(self, influx_client, mocker):
        mocker.patch.object(
            influx_client.session,
            "get",
            side_effect=requests.exceptions.SSLError("Certificate verify failed"),
        )

        with pytest.raises(InfluxConnectionError, match="SSL certificate verification failed"):
            influx_client.fetch_battery_soc()

    def test_influx_error_in_result_json(self, influx_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "results": [
                {
                    "statement_id": 0,
                    "error": "database not found: pv_monitoring",
                }
            ]
        }

        mocker.patch.object(influx_client.session, "get", return_value=mock_response)

        with pytest.raises(InfluxQueryError, match="database not found"):
            influx_client.fetch_battery_soc()

    def test_empty_series_result(self, influx_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "results": [
                {
                    "statement_id": 0,
                }
            ]
        }

        mocker.patch.object(influx_client.session, "get", return_value=mock_response)

        with pytest.raises(InfluxQueryError, match="no series data"):
            influx_client.fetch_battery_soc()

    def test_null_value_in_result(self, influx_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "results": [
                {
                    "statement_id": 0,
                    "series": [
                        {
                            "name": "sungather",
                            "columns": ["time", "max"],
                            "values": [["2026-08-20T11:12:00Z", None]],
                        }
                    ],
                }
            ]
        }

        mocker.patch.object(influx_client.session, "get", return_value=mock_response)

        with pytest.raises(InfluxQueryError, match="is null/None"):
            influx_client.fetch_battery_soc()

    def test_invalid_json(self, influx_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.text = "Not JSON"

        mocker.patch.object(influx_client.session, "get", return_value=mock_response)

        with pytest.raises(InfluxQueryError, match="Failed to parse InfluxDB JSON response"):
            influx_client.fetch_battery_soc()

    def test_verify_ssl_false_disables_warnings(self, mocker):
        mock_disable = mocker.patch("urllib3.disable_warnings")
        InfluxClient(
            url="http://insecure.local:8086",
            user="u",
            password="p",
            db="db",
            query="q",
            verify_ssl=False,
        )
        mock_disable.assert_called_once()

    def test_non_ok_http_status(self, influx_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 400
        mock_response.ok = False
        mock_response.text = "Bad Request"
        mocker.patch.object(influx_client.session, "get", return_value=mock_response)

        with pytest.raises(InfluxQueryError, match="HTTP 400: Bad Request"):
            influx_client.fetch_battery_soc()

    def test_empty_values_list(self, influx_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "results": [
                {
                    "statement_id": 0,
                    "series": [{"name": "s", "columns": ["time", "val"], "values": []}],
                }
            ]
        }
        mocker.patch.object(influx_client.session, "get", return_value=mock_response)

        with pytest.raises(InfluxQueryError, match="series contains no values"):
            influx_client.fetch_battery_soc()

    def test_short_row_format(self, influx_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "results": [
                {
                    "statement_id": 0,
                    "series": [
                        {"name": "s", "columns": ["time"], "values": [["2026-08-20T00:00:00Z"]]}
                    ],
                }
            ]
        }
        mocker.patch.object(influx_client.session, "get", return_value=mock_response)

        with pytest.raises(InfluxQueryError, match="Unexpected row format"):
            influx_client.fetch_battery_soc()

    def test_non_float_value(self, influx_client, mocker):
        mock_response = mocker.Mock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "results": [
                {
                    "statement_id": 0,
                    "series": [
                        {
                            "name": "s",
                            "columns": ["time", "val"],
                            "values": [["2026-08-20T00:00:00Z", "invalid_soc"]],
                        }
                    ],
                }
            ]
        }
        mocker.patch.object(influx_client.session, "get", return_value=mock_response)

        with pytest.raises(InfluxQueryError, match="Could not convert battery level"):
            influx_client.fetch_battery_soc()

    def test_close(self, influx_client, mocker):
        mock_close = mocker.patch.object(influx_client.session, "close")
        influx_client.close()
        mock_close.assert_called_once()
