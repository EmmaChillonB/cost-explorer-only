#!/bin/sh
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

SERVER="cost-explorer-mcp-server"
MCP_TRANSPORT="${MCP_TRANSPORT:-sse}"

# Check if the MCP server process is running
# In a container, the entrypoint runs as PID 1
if ! pgrep -f "awslabs.$SERVER" > /dev/null; then
  echo "$SERVER is not running"
  exit 1
fi

# For SSE/HTTP transports, also check if port 8000 is listening
if [ "$MCP_TRANSPORT" = "sse" ] || [ "$MCP_TRANSPORT" = "streamable-http" ]; then
  # Check if port 8000 is open using /dev/tcp (bash) or nc if available
  if command -v nc > /dev/null 2>&1; then
    if nc -z localhost 8000 2>/dev/null; then
      echo "$SERVER is running and listening on port 8000"
      exit 0
    else
      echo "$SERVER process running but port 8000 not ready"
      exit 1
    fi
  else
    # Fallback: just check process is running
    echo "$SERVER is running (port check skipped - nc not available)"
    exit 0
  fi
fi

# For stdio transport, just process check is enough
echo "$SERVER is running (stdio mode)"
exit 0
