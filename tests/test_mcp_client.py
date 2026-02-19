#!/usr/bin/env python3
"""Simple MCP SSE client to test the Cost Explorer server with multiple clients."""

import asyncio
import json
import aiohttp
from datetime import datetime, timedelta


async def test_mcp_server():
    """Test MCP server via SSE transport with multiple clients."""
    
    base_url = "http://localhost:8000"
    
    # Clients to test
    clients_to_test = [
        "claranet-bastion",
        "profile-nemuru-claranet"
    ]
    
    async with aiohttp.ClientSession() as session:
        # 1. Connect to SSE endpoint and keep it open
        print("1. Connecting to SSE endpoint...")
        
        async with session.get(f"{base_url}/sse") as sse_response:
            # Read the first event to get the session endpoint
            messages_endpoint = None
            async for line in sse_response.content:
                line = line.decode('utf-8').strip()
                if line.startswith("data: "):
                    messages_endpoint = line[6:]
                    print(f"   Got messages endpoint: {messages_endpoint}")
                    break
            
            if not messages_endpoint:
                print("   ERROR: No session endpoint received")
                return
            
            full_messages_url = f"{base_url}{messages_endpoint}"
            
            # 2. Initialize the MCP session
            print("\n2. Initializing MCP session...")
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "test-client",
                        "version": "1.0.0"
                    }
                }
            }
            
            async with session.post(full_messages_url, json=init_request) as resp:
                print(f"   Init response status: {resp.status}")
            
            init_result = await read_sse_response(sse_response)
            print(f"   Server: {init_result.get('result', {}).get('serverInfo', {})}")
            
            # 2b. Send initialized notification
            initialized_notification = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            }
            async with session.post(full_messages_url, json=initialized_notification) as resp:
                pass
            
            # 3. Calculate date range (last month)
            today = datetime.now()
            first_of_this_month = today.replace(day=1)
            last_month_end = first_of_this_month - timedelta(days=1)
            last_month_start = last_month_end.replace(day=1)
            
            print(f"\n3. Testing costs for period: {last_month_start.strftime('%Y-%m-%d')} to {first_of_this_month.strftime('%Y-%m-%d')}")
            print("=" * 70)
            
            # 4. Test each client
            request_id = 10
            for client_id in clients_to_test:
                print(f"\nClient: {client_id}")
                print("-" * 50)
                
                cost_request = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {
                        "name": "get_cost_and_usage",
                        "arguments": {
                            "client_id": client_id,
                            "date_range": {
                                "start_date": last_month_start.strftime("%Y-%m-%d"),
                                "end_date": first_of_this_month.strftime("%Y-%m-%d")
                            },
                            "granularity": "MONTHLY",
                            "group_by": "SERVICE",
                            "metric": "UnblendedCost"
                        }
                    }
                }
                
                async with session.post(full_messages_url, json=cost_request) as resp:
                    if resp.status != 202:
                        print(f"   [ERROR] Request failed: {resp.status}")
                        continue
                
                cost_result = await read_sse_response(sse_response, timeout=60)
                
                # Parse and display results
                if "result" in cost_result:
                    result_content = cost_result["result"]
                    if "structuredContent" in result_content:
                        data = result_content["structuredContent"].get("result", {})
                        if "error" in data:
                            print(f"   [ERROR] {data['error']}")
                        elif "cost_report" in data:
                            report = data["cost_report"]
                            print(f"   [OK] Total Cost: ${report.get('total_cost', 'N/A')}")
                            print(f"   Period: {report.get('period', {})}")
                            print(f"   Top services:")
                            
                            # Show top 5 services by cost
                            if "results" in report:
                                services = []
                                for period_data in report["results"]:
                                    for group in period_data.get("groups", []):
                                        service_name = group.get("group_key", "Unknown")
                                        cost = float(group.get("metrics", {}).get("UnblendedCost", {}).get("amount", 0))
                                        services.append((service_name, cost))
                                
                                services.sort(key=lambda x: x[1], reverse=True)
                                for service, cost in services[:5]:
                                    if cost > 0.01:
                                        print(f"      - {service}: ${cost:.2f}")
                        else:
                            print(f"   Result: {json.dumps(data, indent=2)[:500]}")
                    else:
                        print(f"   Raw: {json.dumps(result_content, indent=2)[:500]}")
                else:
                    print(f"   [ERROR] Unexpected response: {cost_result}")
                
                request_id += 1
            
            # 5. List active sessions
            print("\n" + "=" * 70)
            print("5. Checking active sessions...")
            
            sessions_request = {
                "jsonrpc": "2.0",
                "id": 100,
                "method": "tools/call",
                "params": {
                    "name": "list_active_sessions",
                    "arguments": {}
                }
            }
            
            async with session.post(full_messages_url, json=sessions_request) as resp:
                pass
            
            sessions_result = await read_sse_response(sse_response)
            if "result" in sessions_result:
                content = sessions_result["result"].get("structuredContent", {}).get("result", {})
                print(f"   Active sessions: {content.get('active_sessions', 0)}")
                sessions = content.get("sessions", [])
                if isinstance(sessions, list):
                    for info in sessions:
                        print(f"   - {info.get('client_id', 'N/A')}: role={info.get('role_arn', 'N/A')[:50]}...")
                elif isinstance(sessions, dict):
                    for sid, info in sessions.items():
                        print(f"   - {sid}: role={info.get('role_arn', 'N/A')[:50]}...")


async def read_sse_response(sse_response, timeout=30):
    """Read JSON-RPC response from SSE stream."""
    try:
        end_time = asyncio.get_event_loop().time() + timeout
        async for line in sse_response.content:
            if asyncio.get_event_loop().time() > end_time:
                return {"error": "Timeout waiting for response"}
            line = line.decode('utf-8').strip()
            if line.startswith("data: "):
                data = line[6:]
                try:
                    return json.loads(data)
                except json.JSONDecodeError:
                    continue
    except asyncio.TimeoutError:
        return {"error": "Timeout waiting for response"}
    return {"error": "No response received"}


if __name__ == "__main__":
    asyncio.run(test_mcp_server())

