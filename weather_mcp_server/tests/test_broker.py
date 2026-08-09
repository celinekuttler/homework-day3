import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import weather_broker


def show(label, value):
    print(f"\n=== {label} ===")
    print(json.dumps(value, indent=2, default=str))


# 1. geocoding + current weather
show("current Chicago", weather_broker.get_current_weather("Chicago, IL"))
show("current Austin, TX", weather_broker.get_current_weather("Austin, TX"))
show("current by zip 90210", weather_broker.get_current_weather("90210"))
show("current by lat,lon 48.85,2.35", weather_broker.get_current_weather("48.85,2.35"))

# 2. forecast
show("forecast Chicago 3 days", weather_broker.get_forecast("Chicago, IL", days=3))

# 3. travel recommendation (today + tomorrow + bad date)
rec = weather_broker.get_travel_recommendation("Chicago, IL", "tomorrow")
show("travel rec Chicago tomorrow", rec)
show("travel rec default today", weather_broker.get_travel_recommendation("Austin, TX"))

# 4. bad location -> clean error
try:
    weather_broker.get_current_weather("Atlantis, X")
except Exception as e:
    print(f"\n=== bad location error ===\n{type(e).__name__}: {e}")

# 5. bad date -> clean error
try:
    weather_broker.get_travel_recommendation("Chicago, IL", "2099-01-01")
except Exception as e:
    print(f"\n=== bad date error ===\n{type(e).__name__}: {e}")

# 6. alerts + compare (stretch)
show("alerts Chicago", weather_broker.get_weather_alerts("Chicago, IL"))
show("alerts London (non-US)", weather_broker.get_weather_alerts("London"))
show("compare", weather_broker.compare_weather(["Chicago, IL", "Austin, TX", "Atlantis"]))

print("\nALL TESTS DONE")
