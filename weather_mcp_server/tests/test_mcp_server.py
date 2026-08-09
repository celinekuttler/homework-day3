import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import weather_mcp_server as server


async def main():
    tools = await server.mcp.list_tools()
    print("registered tools:", sorted(t.name for t in tools))

    # Call the @mcp.tool-decorated functions directly to verify the wiring
    # (FastMCP v3 returns the original function from the decorator).
    cases = [
        ("get_current_weather", {"location": "Chicago, IL"}),
        ("get_forecast", {"location": "Chicago, IL", "days": 3}),
        ("get_travel_recommendation", {"location": "Chicago, IL", "date": "tomorrow"}),
        ("get_weather_alerts", {"location": "London"}),
        ("compare_weather", {"locations": ["Chicago, IL", "Austin, TX"]}),
        ("get_current_weather", {"location": "Atlantis, X"}),
    ]
    for name, args in cases:
        fn = getattr(server, name)
        result = await fn(**args) if asyncio.iscoroutinefunction(fn) else fn(**args)
        if name == "get_current_weather" and args["location"] == "Atlantis, X":
            print(f"{name}('{args['location']}') -> clean error:",
                  isinstance(result, dict) and result.get("status") == "error")
        else:
            print(f"{name}{args} -> status: {result.get('status') if isinstance(result, dict) else type(result).__name__}")

    print("MCP SERVER TESTS DONE")


if __name__ == "__main__":
    asyncio.run(main())
