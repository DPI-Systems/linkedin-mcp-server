"""Entry point: the upstream CLI, with the Sales Navigator tools registered.

``create_mcp_server`` is wrapped where both callers look it up
(``cli_main`` imports it by name, the daemon owner imports it from
``server``), so every server this process builds gets the extra tools.
"""

import functools

from linkedin_mcp_server import cli_main, server
from linkedin_mcp_server.server_role import ServerRole

from dpi_salesnav.tools import register_salesnav_tools
from dpi_salesnav.verification_middleware import VerificationMiddleware

_original = server.create_mcp_server


@functools.wraps(_original)
def create_mcp_server(*args, **kwargs):
    mcp = _original(*args, **kwargs)
    role = kwargs.get("role", ServerRole.DIRECT)
    if role.drives_browser:
        register_salesnav_tools(
            mcp,
            tool_timeout=kwargs.get("tool_timeout")
            or server.DEFAULT_TOOL_TIMEOUT_SECONDS,
        )
        # Last, so it is the innermost and sees each tool's coded failure.
        mcp.add_middleware(VerificationMiddleware())
    return mcp


server.create_mcp_server = create_mcp_server
cli_main.create_mcp_server = create_mcp_server

if __name__ == "__main__":
    cli_main.main()
