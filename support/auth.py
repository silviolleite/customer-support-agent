"""Login against the users table, through the Toolbox's `authenticate-user` tool (M-2)."""

from toolbox_core import ToolboxClient

from support.config import TOOLBOX_URL
from support.tool_results import rows as decode_rows


async def login(email: str, password: str) -> dict | None:
    """Return the customer's profile, or None when the email or password is wrong."""
    async with ToolboxClient(TOOLBOX_URL) as client:
        tool = await client.load_tool("authenticate-user")
        rows = decode_rows(await tool(email=email, password=password))
    return rows[0] if rows else None
