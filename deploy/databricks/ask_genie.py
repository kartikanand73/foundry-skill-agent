"""Ask the Genie space a question as YOU (az login), with the same client the agent uses.
usage: python deploy/databricks/ask_genie.py "question" [follow-up ...]"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agent.databricks import DATABRICKS_SCOPE, GenieClient, GenieConfig  # noqa: E402

AZ = "az.cmd" if sys.platform == "win32" else "az"
tok = lambda: subprocess.run([AZ, "account", "get-access-token", "--resource", DATABRICKS_SCOPE.split("/")[0],
                              "--query", "accessToken", "-o", "tsv"], capture_output=True, text=True).stdout.strip()
c = GenieClient(GenieConfig.from_env() or GenieConfig(os.environ["DATABRICKS_HOST"], os.environ["GENIE_SPACE_ID"]),
                token_provider=tok)
for q in sys.argv[1:]:
    print("Q:", q)
    print(json.dumps(c.ask("test", q), indent=1, default=str)[:3000])
