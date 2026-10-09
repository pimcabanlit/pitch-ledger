"""``python -m pitch_ledger.mcp_server <match_dir>``: serve the stats tools over stdio."""

import sys

from pitch_ledger.mcp_server.server import serve

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m pitch_ledger.mcp_server <match_dir>")
    serve(sys.argv[1])
