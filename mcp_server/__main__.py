"""入口：python -m mcp_server"""

from mcp_server.app import run_stdio


def main() -> None:
    run_stdio()


if __name__ == "__main__":
    main()
