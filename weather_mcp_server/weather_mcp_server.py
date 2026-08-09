"""
Weather-prediction MCP server.

Exposes weather-forecast tools over MCP (Model Context Protocol) so a
Databricks Agent Bricks agent can call them like any other tool:
    - get_current_weather(location)
    - get_forecast(location, days)
    - get_travel_recommendation(location, date)
    - get_weather_alerts(location)          (stretch: NWS severe-weather alerts)
    - compare_weather(locations)            (stretch: side-by-side cities)

These tools are backed by the free, keyless Open-Meteo API (see
weather_broker.py), which is the adapter that owns every HTTP call and all
response parsing - the tool functions below stay thin, exactly like
alpaca_mcp_server.py keeps its tools thin over alpaca_broker.py.

No API key is required (Open-Meteo is keyless), so there are no Databricks
secrets to manage for this app. If you later switch to a keyed provider,
follow the _secret()/WorkspaceClient().secrets.get_secret() pattern in
alpaca_broker.py and add the secret env vars to app.yaml.

Deploy this as its own Databricks App (same app.yaml + FastMCP entrypoint
pattern as mcp_server/), so an Agent Bricks agent can register its URL as an
external MCP server:
    https://docs.databricks.com/aws/en/agents/mcp-tools/custom-mcp

Run locally:
    python weather_mcp_server.py
"""

import logging
import os

from fastmcp import FastMCP

import weather_broker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weather-mcp-server")

mcp = FastMCP("weather")


@mcp.tool
def get_current_weather(location: str) -> dict:
    """
    Get current weather conditions for a location.

    Args:
        location: Free-text location - a city name ("Chicago"), "City, State"
            ("Austin, TX"), a US zip code ("90210"), or "lat,lon"
            ("41.88,-87.63").

    Returns:
        A dict with the resolved location, observed_at (local time),
        temperature_c, apparent_temperature_c, humidity_percent,
        wind_speed_kmh, and conditions (e.g. "Partly cloudy").
        On failure: a dict with status "error" and a message.
    """
    try:
        return weather_broker.get_current_weather(location)
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the agent
        logger.warning("get_current_weather(%r) failed: %s", location, exc)
        return {"status": "error", "message": str(exc)}


@mcp.tool
def get_forecast(location: str, days: int = 7) -> dict:
    """
    Get a multi-day weather forecast for a location.

    Args:
        location: Free-text location - a city name ("Chicago"), "City, State"
            ("Austin, TX"), a US zip code ("90210"), or "lat,lon".
        days: Number of forecast days to return (1-16, default 7).

    Returns:
        A dict with the resolved location and a "days" list, one entry per
        day, each with date (YYYY-MM-DD), conditions, high_c, low_c,
        precipitation_chance_percent, precipitation_sum_mm, and
        wind_max_kmh. On failure: a dict with status "error".
    """
    try:
        return weather_broker.get_forecast(location, days)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_forecast(%r) failed: %s", location, exc)
        return {"status": "error", "message": str(exc)}


@mcp.tool
def get_travel_recommendation(location: str, date: str | None = None) -> dict:
    """
    Get derived travel advice for a location on a specific date, based on the
    raw forecast. This is a judgment call, not a passthrough - it applies
    documented thresholds (all Celsius / km/h / % precipitation / mm):

      - umbrella  : precipitation chance >= 40% OR expected total >= 5mm
      - rain gear : precipitation chance >= 60% AND total >= 5mm, OR total >= 20mm
      - jacket    : low temperature < 12 C
      - coat      : low temperature < 0 C
      - sunny     : precipitation chance < 20% AND high >= 22 C
      - windy     : max wind >= 40 km/h

    Args:
        location: Free-text location - a city name ("Chicago"), "City, State"
            ("Austin, TX"), a US zip code ("90210"), or "lat,lon".
        date: Target date as "YYYY-MM-DD", or "today"/"tomorrow" for the
            relative date. Defaults to today if omitted.

    Returns:
        A dict with the resolved location, the resolved date, the raw
        high/low/precipitation figures, the "decisions" list (which rules
        fired and why), and a human-readable "recommendations" list.
        On failure: a dict with status "error".
    """
    try:
        return weather_broker.get_travel_recommendation(location, date)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_travel_recommendation(%r, %r) failed: %s", location, date, exc)
        return {"status": "error", "message": str(exc)}


@mcp.tool
def get_weather_alerts(location: str) -> dict:
    """
    Get active severe-weather alerts for a location (National Weather
    Service, US only).

    Args:
        location: Free-text location - a city name ("Chicago"), "City, State"
            ("Austin, TX"), a US zip code ("90210"), or "lat,lon".

    Returns:
        A dict with the resolved location, alert count, and an "alerts" list
        (each with event, severity, headline, effective, expires). For
        locations outside NWS coverage the alert list is empty with a note.
        On failure: a dict with status "error".
    """
    try:
        return weather_broker.get_weather_alerts(location)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_weather_alerts(%r) failed: %s", location, exc)
        return {"status": "error", "message": str(exc)}


@mcp.tool
def compare_weather(locations: list[str]) -> dict:
    """
    Get current conditions for several locations at once, for side-by-side
    planning.

    Args:
        locations: A list of free-text locations, e.g.
            ["Chicago, IL", "Austin, TX", "New York, NY"].

    Returns:
        A dict with a "results" list (location, temperature_c, conditions,
        wind_speed_kmh) and an "errors" list for any location that could not
        be resolved. On failure: a dict with status "error".
    """
    try:
        return weather_broker.compare_weather(locations)
    except Exception as exc:  # noqa: BLE001
        logger.warning("compare_weather(%r) failed: %s", locations, exc)
        return {"status": "error", "message": str(exc)}


if __name__ == "__main__":
    # Databricks Apps route external HTTP traffic to this port via app.yaml;
    # streamable-http is the transport Databricks' MCP client/gateway expects
    # when hosting a custom MCP server as a Databricks App.
    port = int(os.getenv("DATABRICKS_APP_PORT", os.getenv("PORT", 8000)))
    mcp.run(transport="http", host="0.0.0.0", port=port)
