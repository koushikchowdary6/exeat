# SPDX-License-Identifier: MIT
"""Run the local Merlin mock. It does not call Encord."""

from __future__ import annotations

import argparse

from exeat.integrations.encord.mock import MOCK_NOTICE, MockMerlin, make_mock_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=MOCK_NOTICE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    server = make_mock_server(MockMerlin(), args.host, args.port)
    port = int(server.server_address[1])
    print(f"exeat merlin mock  http://{args.host}:{port}/mcp")
    print(MOCK_NOTICE, flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
