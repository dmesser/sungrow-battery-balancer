"""Grafana Annotations API client for logging battery balancer state changes."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

import requests

logger = logging.getLogger(__name__)


class GrafanaError(Exception):
    """Base exception for Grafana operations."""


class GrafanaAnnotationError(GrafanaError):
    """Exception raised when creating a Grafana annotation fails."""


class GrafanaClient:
    """Client for publishing annotations to Grafana dashboards and panels."""

    def __init__(
        self,
        url: str,
        token: str,
        dashboard_uid: str,
        panel_id: int,
        timeout: float = 10.0,
        verify_ssl: bool = True,
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.dashboard_uid = dashboard_uid
        self.panel_id = panel_id
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.session = requests.Session()

    def create_annotation(
        self,
        text: str,
        tags: Sequence[str] | None = None,
        timestamp_ms: int | None = None,
        dry_run: bool = False,
    ) -> int | None:
        """Post an annotation to the configured Grafana panel.

        Parameters:
            text: Markdown/text content describing what power value was set and why.
            tags: List of tag strings (defaults to ['battery-balancer', 'sungrow', 'sbr256']).
            timestamp_ms: Epoch milliseconds timestamp. Defaults to current time.
            dry_run: If True, simulate the annotation without sending HTTP request.

        Returns:
            Annotation ID if created, or None in dry-run mode.

        Raises:
            GrafanaAnnotationError: If the HTTP request fails or server returns an error.
        """
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)

        tag_list = list(tags) if tags is not None else ["battery-balancer", "sungrow", "sbr256"]

        payload: dict[str, Any] = {
            "dashboardUID": self.dashboard_uid,
            "panelId": self.panel_id,
            "time": timestamp_ms,
            "text": text,
            "tags": tag_list,
        }

        if dry_run:
            logger.info(
                "[DRY-RUN] Grafana annotation simulated (dashboardUID: %s, panelId: %d): %s",
                self.dashboard_uid,
                self.panel_id,
                text,
            )
            return None

        endpoint = f"{self.url}/api/annotations"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        logger.debug("Posting Grafana annotation to %s: %s", endpoint, payload)

        try:
            response = self.session.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        except requests.exceptions.RequestException as exc:
            logger.error("Failed to connect to Grafana API at %s: %s", endpoint, exc)
            raise GrafanaAnnotationError(f"Connection failed: {exc}") from exc

        if response.status_code == 401 or response.status_code == 403:
            raise GrafanaAnnotationError(
                f"Grafana HTTP {response.status_code} Unauthorized: Check service account token."
            )

        if not response.ok:
            raise GrafanaAnnotationError(
                f"Grafana API returned HTTP {response.status_code}: {response.text}"
            )

        try:
            data = response.json()
            annotation_id = data.get("id")
            logger.info(
                "Grafana annotation successfully created (ID: %s) on dashboard %s, panel %d",
                annotation_id,
                self.dashboard_uid,
                self.panel_id,
            )
            return annotation_id
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Grafana annotation created but response parsing failed: %s", exc)
            return None

    def close(self) -> None:
        """Close HTTP session."""
        self.session.close()
