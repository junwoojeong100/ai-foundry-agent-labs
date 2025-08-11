"""
Simple MCP server exposing National Weather Service tools.
Runs with Streamable HTTP transport at http://127.0.0.1:8765/mcp by default.

Run:
  python mcp_server.py

Then connect remotely from the bridge example.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP, Context

# Server configuration
HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "8765"))
MOUNT_PATH = os.environ.get("MCP_MOUNT_PATH", "/mcp")

# Constants for NWS (National Weather Service) API
NWS_API_BASE = "https://api.weather.gov"
# Provide a valid User-Agent per NWS policy, ideally include contact info
NWS_USER_AGENT = os.environ.get(
    "NWS_USER_AGENT",
    "ai-foundry-labs/0.1 (contact: you@example.com)",
)
NWS_TIMEOUT = float(os.environ.get("NWS_TIMEOUT", "15.0"))

mcp = FastMCP(
    name="Weather MCP Server",
    instructions=(
        "This MCP server exposes National Weather Service (api.weather.gov) tools: "
        "nws_point(lat, lon) for grid metadata, nws_forecast(lat, lon, hourly) for forecasts, "
        "and nws_alerts(area, zone, limit) for active alerts."
    ),
)


# --- HTTP helper for NWS ---
async def _nws_get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headers = {
        "Accept": "application/geo+json",
        "User-Agent": NWS_USER_AGENT,
    }
    async with httpx.AsyncClient(timeout=NWS_TIMEOUT, headers=headers) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


# --- NWS Weather Tools ---
@mcp.tool()
async def nws_point(lat: float, lon: float) -> Dict[str, Any]:
    """Lookup NWS grid point metadata for given coordinates."""
    data = await _nws_get_json(f"{NWS_API_BASE}/points/{lat},{lon}")
    p = data.get("properties", {})
    rel = p.get("relativeLocation", {}).get("properties", {})
    return {
        "cwa": p.get("cwa"),
        "gridX": p.get("gridX"),
        "gridY": p.get("gridY"),
        "gridId": p.get("gridId"),
        "forecast": p.get("forecast"),
        "forecastHourly": p.get("forecastHourly"),
        "city": rel.get("city"),
        "state": rel.get("state"),
    }


@mcp.tool()
async def nws_forecast(lat: float, lon: float, hourly: bool = False) -> Dict[str, Any]:
    """Get forecast (or hourly forecast) for given coordinates using NWS."""
    points = await _nws_get_json(f"{NWS_API_BASE}/points/{lat},{lon}")
    props = points.get("properties", {})
    url = props.get("forecastHourly") if hourly else props.get("forecast")
    if not url:
        return {"error": "No forecast URL from points endpoint."}

    fc = await _nws_get_json(url)
    fcp = fc.get("properties", {})
    periods = fcp.get("periods", [])

    def _pick(period: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": period.get("name"),
            "startTime": period.get("startTime"),
            "endTime": period.get("endTime"),
            "temperature": period.get("temperature"),
            "temperatureUnit": period.get("temperatureUnit"),
            "windSpeed": period.get("windSpeed"),
            "windDirection": period.get("windDirection"),
            "shortForecast": period.get("shortForecast"),
            "detailedForecast": period.get("detailedForecast"),
            "probabilityOfPrecipitation": (period.get("probabilityOfPrecipitation") or {}).get("value"),
        }

    return {
        "updated": fcp.get("updateTime"),
        "elevation": (fcp.get("elevation") or {}).get("value"),
        "units": (fcp.get("elevation") or {}).get("unitCode"),
        "periods": [_pick(p) for p in periods],
    }


@mcp.tool()
async def nws_alerts(area: Optional[str] = None, zone: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
    """Get active NWS alerts. Filter by two-letter state area (e.g., CA) or zone code."""
    params: Dict[str, Any] = {"limit": max(1, min(limit, 50))}
    if area:
        params["area"] = area
    if zone:
        params["zone"] = zone

    data = await _nws_get_json(f"{NWS_API_BASE}/alerts/active", params=params)
    features: List[Dict[str, Any]] = data.get("features", [])

    alerts: List[Dict[str, Any]] = []
    for f in features[: params["limit"]]:
        pr = f.get("properties", {})
        alerts.append(
            {
                "id": f.get("id"),
                "event": pr.get("event"),
                "headline": pr.get("headline"),
                "severity": pr.get("severity"),
                "status": pr.get("status"),
                "areaDesc": pr.get("areaDesc"),
                "sent": pr.get("sent"),
                "effective": pr.get("effective"),
                "onset": pr.get("onset"),
                "expires": pr.get("expires"),
                "ends": pr.get("ends"),
                "instructions": pr.get("instruction"),
                "description": pr.get("description"),
                "urgency": pr.get("urgency"),
                "certainty": pr.get("certainty"),
            }
        )

    return {"count": len(alerts), "alerts": alerts}


if __name__ == "__main__":
    # Configure mount path for streamable HTTP
    mcp.settings.host = HOST
    mcp.settings.port = PORT
    mcp.settings.streamable_http_path = MOUNT_PATH

    # Run as Streamable HTTP server
    mcp.run(transport="streamable-http")
