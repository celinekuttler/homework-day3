# Weather Prediction MCP Server + Agent Bricks Agent

Homework 3 for the Databricks AI Bootcamp: a **weather MCP server** built with
FastMCP and an **Agent Bricks agent** that calls it as an external MCP tool.

This app answers natural-language questions such as:

- "What's the weather in Chicago right now?"
- "What should I wear in London tomorrow?"
- "Should we go to Austin or Chicago this weekend?"
- "Is there any severe weather near me?"

## How it satisfies the assignment

| Requirement | Where |
| --- | --- |
| MCP server built with FastMCP, served over streamable HTTP | `weather_mcp_server.py` (`mcp.run(transport="http", ...)`) |
| Every tool annotated with `@mcp.tool` | `weather_mcp_server.py` |
| All HTTP calls and parsing live in a separate adapter module, tools stay thin | `weather_broker.py` (tools contain no `requests` calls) |
| 3+ tools: current conditions, forecast, derived recommendation | `get_current_weather`, `get_forecast`, `get_travel_recommendation` (+ stretch: `get_weather_alerts`, `compare_weather`) |
| Deployed as its own Databricks App | `app.yaml` + `requirements.txt` in this folder |
| Agent Bricks agent using the MCP tools | Steps 6–8 below |
| Clear system prompt (tool order + guardrails) | `agent_system_prompt.md` |
| README with setup steps + 3 example Q&As | this file |
| Weather API + auth method documented | [Weather API & auth](#weather-api--auth) |

## Architecture

```
 User question
      │
      ▼
 Agent Bricks agent  ── enabled tools ──►  External MCP server
 (system prompt in agent_system_prompt.md)     │
                                              ▼
                               weather_mcp_server.py  (FastMCP, 5 @mcp.tool)
                                              │  thin tools, try/except → {"status":"error",...}
                                              ▼
                               weather_broker.py  (adapter: all HTTP + parsing)
                              │             │
                              ▼             ▼
                      Open-Meteo API   NWS API (US alerts)
                      (current, daily   (severe-weather
                       forecast,        alerts)
                       geocoding)
```

The broker owns geocoding (city name, "City, State", US zip, or "lat,lon"),
all HTTP requests, WMO weather-code → text mapping, and the decision rules for
the recommendation tool. The FastMCP tool functions are thin wrappers that
catch errors and return a clean `{"status": "error", "message": ...}` dict so
the agent never sees a stack trace.

## Tools

| Tool | Description | Example arguments |
| --- | --- | --- |
| `get_current_weather` | Current temperature, apparent temperature, humidity, wind, conditions | `{"location": "Chicago, IL"}` |
| `get_forecast` | 1–16 day daily forecast (high/low, conditions, precip %, precip mm, max wind) | `{"location": "London", "days": 3}` |
| `get_travel_recommendation` | Derived advice for a day: umbrella / rain gear / jacket / coat / sunny / windy, with the raw numbers behind each decision | `{"location": "Chicago, IL", "date": "tomorrow"}` |
| `get_weather_alerts` | Active NWS severe-weather alerts (US only; non-US → empty list + note) | `{"location": "Chicago, IL"}` |
| `compare_weather` | Side-by-side current conditions for several locations | `{"locations": ["Austin, TX", "Chicago, IL"]}` |

Locations are free text: a city (`Chicago`), `City, State` (`Austin, TX`), a
US zip code (`90210`), or explicit `lat,lon` (`41.88,-87.63`). All units are
metric (Celsius, km/h, mm).

## Weather API & auth

- **Open-Meteo** (<https://open-meteo.com/>) powers geocoding, current
  conditions, and forecasts. It is **free and keyless** (no API key required),
  so this app needs **no Databricks secrets**.
- **National Weather Service API** (<https://api.weather.gov/>) powers
  severe-weather alerts for US locations; also keyless.
- Because no key is involved there is nothing to protect or rotate. If the
  app were later switched to a keyed provider, the reference repo's pattern
  applies: load the secret with `WorkspaceClient().secrets.get_secret(...)`
  (see `_secret()` in the reference `alpaca_broker.py`) and reference it via
  env vars in `app.yaml` — never hardcode credentials.

## File layout

```
weather_mcp_server/
├── weather_mcp_server.py      # FastMCP server (5 tools, streamable HTTP entrypoint)
├── weather_broker.py          # adapter: geocoding, Open-Meteo, NWS, decision rules
├── app.yaml                   # Databricks App definition
├── requirements.txt           # fastmcp>=3.2.0, requests>=2.31.0
├── agent_system_prompt.md     # system prompt to paste into the Agent Bricks agent
├── README.md                  # this file
└── tests/
    ├── test_broker.py         # live tests against the broker
    ├── test_mcp_server.py     # verifies tool registration + calls
    └── test_http_smoke.py     # full MCP streamable-HTTP handshake against a booted server
```

## Run it locally

```bash
pip install -r weather_mcp_server/requirements.txt
python weather_mcp_server/weather_mcp_server.py
```

The server listens on `http://0.0.0.0:8000` (`PORT`/`DATABRICKS_APP_PORT`
override). You can then point the MCP Inspector (or any MCP client) at
`http://127.0.0.1:8000/mcp`.

## Deploy on Databricks

1. **Push this repo to GitHub** (already on `main` of
   `celinekuttler/databricks-lakebase-app-day-3`). Commit the
   `weather_mcp_server/` folder.
2. In your Databricks workspace, add the repo via **Git folders**:
   left sidebar → *Git folders* → *Add Git folder* → paste the GitHub URL and
   authenticate. This syncs the repo into your workspace.
3. **Create the App**: left sidebar → *Apps* → *Create App* → give it a name
   and set the **App directory** to the `weather_mcp_server` folder inside the
   synced Git folder (this folder contains its own `app.yaml`, so it deploys
   as its own App).
4. Databricks builds the App, installs `requirements.txt`, and starts
   `python weather_mcp_server.py` (the `command` in `app.yaml`). Wait for the
   App's status to show **Ready**.
5. Copy the App's **URL** (looks like
   `https://<workspace>.databricksapps.com/app/<app>`) — this is your external
   MCP endpoint (`<App-URL>/mcp`). Requests to Databricks Apps are served over
   HTTPS, which the MCP spec requires.

## Build the Agent Bricks agent

6. Left sidebar → **Agent Bricks** → *Create Agent* → name it, e.g.
   `weather-agent`. Model defaults are fine.
7. **Add the MCP tools**: in the agent's *Tools* section, add **MCP** →
   *External MCP Server* → name it `weather` and set the **URL** to
   `<App-URL>/mcp` (protocol: streamable HTTP). The 5 tools should appear and
   be enabled.
8. **Paste the system prompt**: open the agent's system prompt and paste the
   full contents of `agent_system_prompt.md`. It tells the agent which tool to
   reach for, the location formats it accepts, the units, and the guardrails
   (never invent data, resolve bad locations by asking, US-only alerts).
9. **Test it** with the three questions below, then create and publish the
   agent as you would for the assignment.

> If the tools do not show up, check the App URL is reachable (the App must be
> **Ready**, not *Building*/*Failed*), and that the endpoint is exactly
> `<App-URL>/mcp`.

## Example questions & answers

These are real outputs from the tools (August 9, 2026).

### 1. "What should I wear in Chicago tomorrow?"

Agent calls `get_travel_recommendation(location="Chicago, IL", date="tomorrow")`:

```json
{
  "location": "Chicago", "date": "2026-08-10",
  "conditions": "Violent rain showers", "high_c": 28.6, "low_c": 22.3,
  "precipitation_chance_percent": 43, "precipitation_sum_mm": 39.38,
  "wind_max_kmh": 18.0,
  "decisions": [
    {"rule": "umbrella",    "triggered": true,  "explanation": "precipitation chance is 43% and 39.4mm expected"},
    {"rule": "rain gear",   "triggered": true,  "explanation": "43% chance and 39.4mm expected"},
    {"rule": "jacket",      "triggered": false, "explanation": "low of 22.3 C"},
    {"rule": "coat",        "triggered": false, "explanation": "low of 22.3 C"},
    {"rule": "sunny",       "triggered": false, "explanation": "43% chance and high of 28.6 C"},
    {"rule": "windy",       "triggered": false, "explanation": "wind gusts up to 18 km/h"}
  ]
}
```

**Agent answer:** "Wet day in Chicago tomorrow — bring an umbrella, and I'd go
with rain gear too: ~43% chance of rain with about 39 mm expected (violent
showers). It stays warm (high 29 °C / low 22 °C), so no coat needed."

### 2. "Should we go to Austin or Chicago this weekend?"

Agent calls `compare_weather(locations=["Austin, TX", "Chicago, IL"])`:

```json
{"results": [
  {"location": "Austin",  "temperature_c": 35.7, "conditions": "Clear sky", "wind_speed_kmh": 12.6},
  {"location": "Chicago", "temperature_c": 31.2, "conditions": "Overcast", "wind_speed_kmh": 19.4}
], "errors": []}
```

**Agent answer:** "Austin is the sunnier pick right now: 36 °C and clear,
light wind. Chicago is 31 °C but overcast. If you want sun, go Austin."

### 3. "Is there any severe weather in Chicago right now?"

Agent calls `get_weather_alerts(location="Chicago, IL")`:

```json
{"count": 2, "alerts": [
  {"event": "Severe Thunderstorm Watch", "severity": "Severe",
   "headline": "Severe Thunderstorm Watch issued August 9 at 1:47PM CDT until August 9 at 4:00PM CDT by NWS Chicago IL"},
  {"event": "Flood Advisory", "severity": "Minor",
   "headline": "Flood Advisory issued August 9 at 1:28PM CDT until August 9 at 4:30PM CDT by NWS Chicago IL"}
]}
```

**Agent answer:** "Yes — there's an active **Severe Thunderstorm Watch** (until
4:00 PM CDT) and a **Minor Flood Advisory** (until 4:30 PM CDT) from NWS
Chicago. Keep an eye on the sky and stay indoors during the thunderstorm
window." *(Alerts are a US-only NWS service.)*

## Notes

- The broker's recommendation rules (all in `weather_broker.py`): umbrella if
  precip chance ≥ 40% **or** total ≥ 5 mm; rain gear if (chance ≥ 60% and
  total ≥ 5 mm) **or** total ≥ 20 mm; jacket if low < 12 °C; coat if low
  < 0 °C; sunny if chance < 20% and high ≥ 22 °C; windy if wind ≥ 40 km/h.
- Tests: `python tests/test_broker.py` (live API checks), and the HTTP smoke
  test that boots the real server and drives initialize → tools/list →
  tools/call over streamable HTTP.
