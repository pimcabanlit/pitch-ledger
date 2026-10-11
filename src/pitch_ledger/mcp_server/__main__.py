"""``python -m pitch_ledger.mcp_server <match_dir> [season_file]``: serve the stats tools
over stdio (``season_file``: the earlier matches' records, JSON lines)."""

import sys

from pitch_ledger.mcp_server.server import serve

if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: python -m pitch_ledger.mcp_server <match_dir> [season_file]")
    serve(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None)
