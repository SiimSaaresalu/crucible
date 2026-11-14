#!/bin/bash
# GRADE_TOKEN comes from [verifier.env]; the agent never sees it.
mkdir -p /logs/verifier
apt-get update -qq && apt-get install -y -qq curl >/dev/null
echo "--- no token:"
curl -s -o /dev/null -w "%{http_code}\n" http://mcp-server:8000/grade | tee /logs/verifier/notoken.txt
RESP=$(curl -s -H "x-grade-token: ${GRADE_TOKEN}" http://mcp-server:8000/grade)
echo "$RESP" | tee /logs/verifier/grade.json
python3 -c "import json,sys; print(json.loads(sys.argv[1])['reward'])" "$RESP" > /logs/verifier/reward.txt || echo 0 > /logs/verifier/reward.txt
