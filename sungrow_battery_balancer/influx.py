"""InfluxDB 1.8.x client for querying battery State-of-Charge (SoC)."""

from __future__ import annotations

import logging
from typing import Any

import requests
import urllib3

logger = logging.getLogger(__name__)


class InfluxError(Exception):
    """Base exception for InfluxDB operations."""


class InfluxConnectionError(InfluxError):
    """Exception raised when connection to InfluxDB fails."""


class InfluxQueryError(InfluxError):
    """Exception raised when an InfluxQL query fails or returns invalid data."""


class InfluxClient:
    """Client for InfluxDB 1.8.x HTTP API using InfluxQL."""

    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        db: str,
        query: str,
        verify_ssl: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.user = user
        self.password = password
        self.db = db
        self.query = query
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session = requests.Session()

        if not self.verify_ssl:
            # Suppress unverified HTTPS request warnings when explicitly disabled
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def fetch_battery_soc(self, query_override: str | None = None) -> float:
        """Execute InfluxQL query to retrieve the latest battery SoC as a float percentage.

        Returns:
            Battery SoC float (e.g. 85.4 for 85.4%).

        Raises:
            InfluxConnectionError: If network connection or HTTP request times out.
            InfluxQueryError: If the server returns an error or unparseable/empty data.
        """
        endpoint = f"{self.url}/query"
        active_query = query_override or self.query
        params = {
            "db": self.db,
            "q": active_query,
        }

        logger.debug(
            "Querying InfluxDB at %s for DB '%s' with query: %s",
            endpoint,
            self.db,
            active_query,
        )

        try:
            response = self.session.get(
                endpoint,
                params=params,
                auth=(self.user, self.password),
                verify=self.verify_ssl,
                timeout=self.timeout,
            )
        except requests.exceptions.SSLError as exc:
            logger.error("SSL Verification error connecting to InfluxDB at %s: %s", self.url, exc)
            raise InfluxConnectionError(f"SSL certificate verification failed: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            logger.error("Failed to connect to InfluxDB at %s: %s", self.url, exc)
            raise InfluxConnectionError(f"Connection failed: {exc}") from exc

        if response.status_code == 401 or response.status_code == 403:
            raise InfluxQueryError(
                f"InfluxDB HTTP {response.status_code} Unauthorized: Check username and password."
            )

        if not response.ok:
            raise InfluxQueryError(
                f"InfluxDB query failed with HTTP {response.status_code}: {response.text}"
            )

        try:
            data: dict[str, Any] = response.json()
        except Exception as exc:
            raise InfluxQueryError(
                f"Failed to parse InfluxDB JSON response: {exc}. Response text: {response.text[:200]}"
            ) from exc

        return self._extract_soc_from_response(data)

    def _extract_soc_from_response(self, data: dict[str, Any]) -> float:
        """Extract float SoC value from InfluxDB JSON result payload."""
        results = data.get("results")
        if not results or not isinstance(results, list):
            raise InfluxQueryError(f"Malformed InfluxDB response (missing results): {data}")

        first_result = results[0]
        if "error" in first_result:
            raise InfluxQueryError(f"InfluxDB returned error: {first_result['error']}")

        series_list = first_result.get("series")
        if not series_list or not isinstance(series_list, list):
            raise InfluxQueryError(
                f"InfluxDB query returned no series data (empty result). Data: {data}"
            )

        first_series = series_list[0]
        values = first_series.get("values")
        if not values or not isinstance(values, list) or len(values) == 0:
            raise InfluxQueryError("InfluxDB series contains no values.")

        first_row = values[0]
        # In InfluxQL, column 0 is usually 'time' and column 1 is the queried value.
        if len(first_row) < 2:
            raise InfluxQueryError(f"Unexpected row format in InfluxDB series values: {first_row}")

        raw_val = first_row[1]
        if raw_val is None:
            raise InfluxQueryError("Latest battery level returned from InfluxDB is null/None.")

        try:
            soc = float(raw_val)
        except (ValueError, TypeError) as exc:
            raise InfluxQueryError(
                f"Could not convert battery level '{raw_val}' to float: {exc}"
            ) from exc

        return soc

    def close(self) -> None:
        """Close HTTP session."""
        self.session.close()
