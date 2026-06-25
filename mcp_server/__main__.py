"""Entry point for `python -m mcp_server`."""

from mcp_server.server import create_app


if __name__ == "__main__":
    create_app().run()
