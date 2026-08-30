"""MCP description + agent skill shipped with the connector."""

from __future__ import annotations

from pathlib import Path

DESCRIPTION = (
    "Bring Fast is a per-user grocery MCP for Dubai. "
    "It searches live supermarket catalogs, ranks spend from official invoices, "
    "forecasts what to buy next, and talks to official store carts "
    "(Grandiose, Union Coop, Carrefour). OAuth is the same email/password as the dashboard. "
    "No local cart. "
    "This same connector also exposes X (Twitter) tools for the host's developer app "
    "(read @ilTrumpista, search recent posts, and create tweets with x_post)."
)

SKILL_NAME = "bring-fast-agent"
SKILL_TITLE = "Bring Fast agent skill"
RESOURCE_URI = "bringfast://skill/agent"


def skill_text() -> str:
    path = Path(__file__).with_name("agent_skill.md")
    return path.read_text(encoding="utf-8")


def instructions(email: str) -> str:
    who = (email or "").strip() or "this user"
    return (
        f"{DESCRIPTION} Signed-in account: {who} only.\n\n"
        f"{skill_text().strip()}\n"
    )


def prompts() -> list[dict]:
    return [
        {
            "name": SKILL_NAME,
            "title": SKILL_TITLE,
            "description": DESCRIPTION,
        }
    ]


def resources() -> list[dict]:
    return [
        {
            "uri": RESOURCE_URI,
            "name": SKILL_TITLE,
            "description": DESCRIPTION,
            "mimeType": "text/markdown",
        }
    ]


def prompt_get(name: str) -> dict | None:
    if (name or "").strip() != SKILL_NAME:
        return None
    return {
        "description": DESCRIPTION,
        "messages": [
            {
                "role": "user",
                "content": {"type": "text", "text": skill_text()},
            }
        ],
    }


def resource_read(uri: str) -> dict | None:
    if (uri or "").strip() != RESOURCE_URI:
        return None
    return {
        "contents": [
            {
                "uri": RESOURCE_URI,
                "mimeType": "text/markdown",
                "text": skill_text(),
            }
        ]
    }
