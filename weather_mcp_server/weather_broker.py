"""
weather_broker.py - Open-Meteo weather adapter backing the weather MCP server.

This module is the "broker/adapter" for weather data, in the same role
alpaca_broker.py plays for the Alpaca MCP server: every HTTP call and all
response parsing lives here, and the FastMCP tool functions in
weather_mcp_server.py stay thin one-liners over these functions.

Data source: Open-Meteo (https://open-meteo.com) - a free, keyless weather
API with generous non-commercial limits (~10k calls/day). No API key, no
signup, and therefore no Databricks secret is needed for any of this.

Geocoding: Open-Meteo's geocoding API resolves free-text locations
("Chicago", "90210", "48.85,2.35") to lat/lon before the forecast API is
called, so the MCP tools can accept a city name, zip, or lat/lon string.

Error convention: every public function raises one of the two typed
exceptions below on failure, so the MCP server can translate them into a
clean {"status": "error", "message": ...} dict that the agent can react to
instead of a stack trace.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Public configuration / constants
# ---------------------------------------------------------------------------

GEOCODING_API = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
NWS_ALERTS_API = "https://api.weather.gov/alerts/active"

USER_AGENT = "databricks-weather-mcp-server/1.0 (contact: student@example.com)"
_TIMEOUT = 15  # seconds

# WMO weather interpretation codes -> human-readable conditions.
# https://open-meteo.com/en/docs
_WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

# Thresholds used by get_travel_recommendation(). Kept at module level so the
# reasoning is transparent and easy to tune.
UMBRELLA_PRECIP_CHANCE = 40      # % chance of precipitation -> pack umbrella
UMBRELLA_PRECIP_MM = 5.0         # expected total mm -> pack umbrella regardless of chance
RAIN_GEAR_CHANCE = 60            # % chance AND >= 5mm total -> avoid outdoor plans
RAIN_GEAR_MM = 20.0              # expected total mm -> rain gear regardless of chance
JACKET_LOW_TEMP = 12.0           # Celsius low -> bring a jacket
COAT_LOW_TEMP = 0.0              # Celsius low -> bring a warm coat
SUNNY_DAY_HIGH_TEMP = 22.0       # Celsius high, low rain chance -> sunglasses/sunscreen
SUNNY_MAX_PRECIP_CHANCE = 20.0   # max % chance for a "sunny" recommendation
WINDY_WIND_SPEED = 40.0          # km/h max wind -> strong winds advisory

# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class LocationNotFoundError(Exception):
    """Raised when a location string cannot be geocoded."""


class WeatherAPIError(Exception):
    """Raised when the weather API call itself fails (network, HTTP error)."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _conditions(weather_code: int | None) -> str:
    """Map a WMO weather code to a human-readable condition string."""
    if weather_code is None:
        return "Unknown"
    return _WMO_CODES.get(int(weather_code), f"Weather code {weather_code}")


def _get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET + JSON-parse, raising WeatherAPIError on any transport/HTTP failure."""
    try:
        resp = requests.get(url, params=params, timeout=_TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as exc:
        raise WeatherAPIError(f"Weather API returned HTTP {resp.status_code} for {url}") from exc
    except requests.exceptions.RequestException as exc:
        raise WeatherAPIError(f"Could not reach weather API {url}: {exc}") from exc
    except ValueError as exc:
        raise WeatherAPIError(f"Weather API returned non-JSON response from {url}") from exc


def _geocode(location: str) -> dict[str, Any]:
    """
    Resolve a free-text location to coordinates via Open-Meteo geocoding.

    Accepts a city name ("Chicago"), "City, State" ("Austin, TX"),
    a zip/postal code ("90210"), or a "lat,lon" string ("41.88,-87.63").

    Returns a dict with name, country, region, latitude, longitude, timezone.

    Raises:
        LocationNotFoundError: if the location cannot be resolved.
        WeatherAPIError: if the geocoding API call fails.
    """
    location = (location or "").strip()
    if not location:
        raise LocationNotFoundError("No location provided")

    # "lat,lon" fast path - skip geocoding for explicit coordinates.
    if "," in location:
        parts = [p.strip() for p in location.split(",")]
        if len(parts) == 2:
            try:
                lat, lon = float(parts[0]), float(parts[1])
            except ValueError:
                pass
            else:
                return {
                    "query": location,
                    "name": f"{lat:.2f}, {lon:.2f}",
                    "country": None,
                    "region": None,
                    "latitude": lat,
                    "longitude": lon,
                    "timezone": "auto",
                }

    params = {"name": location, "count": 1, "language": "en", "format": "json"}
    data = _get_json(GEOCODING_API, params)

    results = data.get("results") or []
    if not results:
        raise LocationNotFoundError(
            f"Could not resolve location {location!r}. Try a city name (e.g. "
            f"'Chicago'), 'City, State' (e.g. 'Austin, TX'), a US zip code, "
            f"or explicit 'lat,lon' coordinates."
        )

    best = results[0]
    return {
        "query": location,
        "name": best.get("name"),
        "country": best.get("country"),
        "region": best.get("admin1"),
        "latitude": best["latitude"],
        "longitude": best["longitude"],
        "timezone": best.get("timezone", "auto"),
    }


def _resolve(location: str) -> dict[str, Any]:
    """Geocode a location and return it plus ready-to-use forecast query params."""
    geo = _geocode(location)
    params = {
        "latitude": geo["latitude"],
        "longitude": geo["longitude"],
        "timezone": "auto",
    }
    return geo, params


# ---------------------------------------------------------------------------
# Public broker API (called by the MCP tools)
# ---------------------------------------------------------------------------


def get_current_weather(location: str) -> dict[str, Any]:
    """
    Current conditions for a location: temperature, apparent temperature,
    humidity, wind, and a human-readable condition string.

    Args:
        location: Free-text location (city name, zip, or "lat,lon").

    Returns:
        dict with location info, observed_at (local ISO datetime),
        temperature_c, apparent_temperature_c, humidity_percent,
        wind_speed_kmh, conditions, weather_code.

    Raises:
        LocationNotFoundError, WeatherAPIError.
    """
    geo, params = _resolve(location)
    params["current"] = (
        "temperature_2m,relative_humidity_2m,apparent_temperature,"
        "weather_code,wind_speed_10m"
    )
    data = _get_json(FORECAST_API, params)

    current = data.get("current") or {}
    if not current:
        raise WeatherAPIError("Weather API returned no 'current' data")

    return {
        "status": "success",
        "location": geo["name"],
        "region": geo.get("region"),
        "country": geo.get("country"),
        "coordinates": {
            "latitude": round(geo["latitude"], 4),
            "longitude": round(geo["longitude"], 4),
        },
        "observed_at": current.get("time"),
        "temperature_c": current.get("temperature_2m"),
        "apparent_temperature_c": current.get("apparent_temperature"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "wind_speed_kmh": current.get("wind_speed_10m"),
        "conditions": _conditions(current.get("weather_code")),
        "weather_code": current.get("weather_code"),
    }


def get_forecast(location: str, days: int = 7) -> dict[str, Any]:
    """
    Multi-day forecast for a location: high/low temperature, precipitation
    chance and total, max wind, and a condition string per day.

    Args:
        location: Free-text location (city name, zip, or "lat,lon").
        days: Number of forecast days to return (1-16, default 7).

    Returns:
        dict with location info and a "days" list, each entry with date
        (YYYY-MM-DD), conditions, high_c, low_c,
        precipitation_chance_percent, precipitation_sum_mm, wind_max_kmh.

    Raises:
        LocationNotFoundError, WeatherAPIError.
    """
    days = max(1, min(int(days), 16))
    geo, params = _resolve(location)
    params["current"] = "temperature_2m"
    params["daily"] = (
        "weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,precipitation_sum,wind_speed_10m_max"
    )
    params["forecast_days"] = days
    data = _get_json(FORECAST_API, params)

    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    if not dates:
        raise WeatherAPIError("Weather API returned no 'daily' data")

    days_out = []
    for i, date in enumerate(dates):
        days_out.append(
            {
                "date": date,
                "conditions": _conditions(_safe_get(daily, "weather_code", i)),
                "high_c": _safe_get(daily, "temperature_2m_max", i),
                "low_c": _safe_get(daily, "temperature_2m_min", i),
                "precipitation_chance_percent": _safe_get(
                    daily, "precipitation_probability_max", i
                ),
                "precipitation_sum_mm": _safe_get(daily, "precipitation_sum", i),
                "wind_max_kmh": _safe_get(daily, "wind_speed_10m_max", i),
            }
        )

    return {
        "status": "success",
        "location": geo["name"],
        "region": geo.get("region"),
        "country": geo.get("country"),
        "days": days_out,
        "unit_system": "metric",
    }


def get_travel_recommendation(location: str, date: str | None = None) -> dict[str, Any]:
    """
    Derived travel advice for a specific date, built from the raw forecast.

    This is a judgment call, not a passthrough of the API: it applies the
    documented thresholds below to the forecast for the requested day and
    explains which rules fired.

    Rules applied (all thresholds are Celsius / km/h / % precipitation / mm):
      - umbrella  : precipitation chance >= 40% OR expected total >= 5mm
      - rain gear : precipitation chance >= 60% AND total >= 5mm, OR total >= 20mm
      - jacket    : low temperature < 12 C
      - coat      : low temperature < 0 C
      - sunny     : precipitation chance < 20% AND high temperature >= 22 C
      - windy     : max wind >= 40 km/h

    Args:
        location: Free-text location (city name, zip, or "lat,lon").
        date: Target date as "YYYY-MM-DD", or "today"/"tomorrow" for the
            relative date. Defaults to today if omitted.

    Returns:
        dict with location info, the resolved date, the raw high/low and
        precipitation figures, a list of the decisions made (rule, triggered,
        explanation), and a human-readable "recommendations" list.

    Raises:
        LocationNotFoundError, WeatherAPIError, ValueError (bad date).
    """
    forecast = get_forecast(location, days=16)
    day = _pick_day(forecast["days"], date)

    precip_chance = day["precipitation_chance_percent"] or 0
    precip_sum = day["precipitation_sum_mm"] or 0
    high = day["high_c"]
    low = day["low_c"]
    wind = day["wind_max_kmh"] or 0

    rules = [
        {
            "rule": "umbrella",
            "description": "precipitation chance >= 40% or total >= 5mm",
            "triggered": precip_chance >= UMBRELLA_PRECIP_CHANCE or precip_sum >= UMBRELLA_PRECIP_MM,
            "explanation": f"precipitation chance is {precip_chance:.0f}% and {precip_sum:.1f}mm expected",
        },
        {
            "rule": "rain gear",
            "description": "chance >= 60% and total >= 5mm, or total >= 20mm",
            "triggered": (
                (precip_chance >= RAIN_GEAR_CHANCE and precip_sum >= UMBRELLA_PRECIP_MM)
                or precip_sum >= RAIN_GEAR_MM
            ),
            "explanation": f"{precip_chance:.0f}% chance and {precip_sum:.1f}mm expected",
        },
        {
            "rule": "jacket",
            "description": "low temperature < 12 C",
            "triggered": low is not None and low < JACKET_LOW_TEMP,
            "explanation": f"low of {low} C",
        },
        {
            "rule": "coat",
            "description": "low temperature < 0 C",
            "triggered": low is not None and low < COAT_LOW_TEMP,
            "explanation": f"low of {low} C",
        },
        {
            "rule": "sunny",
            "description": "precipitation chance < 20% and high >= 22 C",
            "triggered": (
                precip_chance < SUNNY_MAX_PRECIP_CHANCE
                and high is not None
                and high >= SUNNY_DAY_HIGH_TEMP
            ),
            "explanation": f"{precip_chance:.0f}% chance and high of {high} C",
        },
        {
            "rule": "windy",
            "description": "max wind >= 40 km/h",
            "triggered": wind >= WINDY_WIND_SPEED,
            "explanation": f"wind gusts up to {wind:.0f} km/h",
        },
    ]

    recommendations = [r["explanation"] for r in rules if r["triggered"]]
    if not recommendations:
        recommendations = ["No special weather gear needed - mild conditions expected."]

    return {
        "status": "success",
        "location": forecast["location"],
        "region": forecast.get("region"),
        "country": forecast.get("country"),
        "date": day["date"],
        "conditions": day["conditions"],
        "high_c": high,
        "low_c": low,
        "precipitation_chance_percent": precip_chance,
        "precipitation_sum_mm": precip_sum,
        "wind_max_kmh": wind,
        "decisions": rules,
        "recommendations": recommendations,
    }


def get_weather_alerts(location: str) -> dict[str, Any]:
    """
    Active severe-weather alerts (NWS, US only) for a location.

    NWS coverage is US-only. For locations outside NWS coverage this returns
    an empty alert list with a note, so the agent can answer honestly instead
    of guessing.

    Args:
        location: Free-text location (city name, zip, or "lat,lon").

    Returns:
        dict with location info, count, and an "alerts" list (each with event,
        severity, headline, effective, expires).

    Raises:
        LocationNotFoundError, WeatherAPIError.
    """
    geo, _ = _resolve(location)
    try:
        data = _get_json(
            NWS_ALERTS_API,
            {"point": f"{geo['latitude']:.2f},{geo['longitude']:.2f}"},
        )
    except WeatherAPIError as exc:
        # NWS returns 400/404 for points outside US coverage - treat that as
        # "no alerts available" with an explanatory note, not a hard error, so
        # the agent can still answer honestly.
        if any(code in str(exc) for code in ("HTTP 400", "HTTP 404")):
            return {
                "status": "success",
                "location": geo["name"],
                "region": geo.get("region"),
                "country": geo.get("country"),
                "count": 0,
                "alerts": [],
                "note": "NWS alerts only cover the United States; this location is outside NWS coverage.",
            }
        raise

    features = data.get("features") or []
    alerts = []
    for feature in features:
        props = feature.get("properties") or {}
        alerts.append(
            {
                "event": props.get("event"),
                "severity": props.get("severity"),
                "headline": props.get("headline"),
                "effective": props.get("effective"),
                "expires": props.get("expires"),
            }
        )

    note = None
    if not features and geo.get("country") not in (None, "United States"):
        note = "NWS alerts only cover the United States; this location is outside NWS coverage."

    return {
        "status": "success",
        "location": geo["name"],
        "region": geo.get("region"),
        "country": geo.get("country"),
        "count": len(alerts),
        "alerts": alerts,
        "note": note,
    }


def compare_weather(locations: list[str]) -> dict[str, Any]:
    """
    Current conditions for several locations at once (handy for side-by-side
    travel planning). Each failed location is reported individually so one
    bad entry doesn't fail the whole call.

    Args:
        locations: List of free-text locations.

    Returns:
        dict with a "results" list (location, temperature_c, conditions,
        wind_speed_kmh) for each resolvable location and an "errors" list for
        the ones that couldn't be resolved.

    Raises:
        WeatherAPIError for transport-level failures (never for a single
        unresolvable location).
    """
    if not locations:
        return {"status": "success", "results": [], "errors": ["No locations provided"]}

    results = []
    errors = []
    for location in locations:
        try:
            current = get_current_weather(location)
        except LocationNotFoundError as exc:
            errors.append(str(exc))
            continue
        results.append(
            {
                "location": current["location"],
                "temperature_c": current["temperature_c"],
                "conditions": current["conditions"],
                "wind_speed_kmh": current["wind_speed_kmh"],
            }
        )

    return {"status": "success", "results": results, "errors": errors}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _safe_get(daily: dict[str, list], key: str, index: int) -> Any:
    """Return daily[key][index] if present, else None (Open-Meteo uses nulls)."""
    values = daily.get(key)
    if values is None or index >= len(values):
        return None
    return values[index]


def _pick_day(days: list[dict[str, Any]], date: str | None) -> dict[str, Any]:
    """Select the forecast day matching `date` (ISO date, 'today', or 'tomorrow')."""
    if not days:
        raise WeatherAPIError("Forecast returned no days to evaluate")

    if date is None or str(date).strip().lower() in ("", "today"):
        return days[0]
    if str(date).strip().lower() == "tomorrow":
        if len(days) < 2:
            raise ValueError("The forecast does not include tomorrow yet - try a date within the next 16 days")
        return days[1]

    target = str(date).strip()
    for day in days:
        if day["date"] == target:
            return day

    first, last = days[0]["date"], days[-1]["date"]
    raise ValueError(
        f"Date {target!r} is outside the forecast window ({first}..{last}). "
        f"Use a 'YYYY-MM-DD' date in that range, or 'today'/'tomorrow'."
    )
