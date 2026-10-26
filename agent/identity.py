"""The signed-in user, as passed by the web app (Entra object id, name, app roles).

Trust model: the web app reads the user from App Service authentication (headers the platform sets
after Entra sign-in) and sends it in the request body. APIM only accepts calls that carry the web
app's own managed-identity token (validate-jwt), so only the web app can supply this. The agent
treats it as *asserted by a trusted caller*, not as a token it verified itself.
"""
from __future__ import annotations

import contextvars
import hashlib
import re
from dataclasses import dataclass, field

_ROLE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_OID = re.compile(r"^[0-9a-fA-F-]{8,64}$")


@dataclass(frozen=True)
class UserContext:
    oid: str
    name: str = ""
    roles: tuple[str, ...] = field(default_factory=tuple)

    @property
    def id_hash(self) -> str:
        """Pseudonymous id for logs and traces (never the raw oid or name)."""
        return hashlib.sha256(self.oid.lower().encode()).hexdigest()[:16]

    def has(self, role: str) -> bool:
        return role in self.roles

    def echo(self) -> dict:
        return {"name": self.name, "roles": list(self.roles), "user_id_hash": self.id_hash}


def parse_user(raw) -> UserContext | None:
    """Validate the body's `user` object; anything malformed is ignored (treated as anonymous)."""
    if not isinstance(raw, dict):
        return None
    oid = raw.get("oid")
    if not isinstance(oid, str) or not _OID.match(oid):
        return None
    name = raw.get("name") if isinstance(raw.get("name"), str) else ""
    roles = raw.get("roles") if isinstance(raw.get("roles"), list) else []
    clean = tuple(sorted({r for r in roles if isinstance(r, str) and _ROLE.match(r)}))[:20]
    return UserContext(oid=oid, name=name[:200], roles=clean)


# The user of the question being handled. Per request (each request runs in its own copied context),
# so concurrent users never see each other's identity.
current_user: contextvars.ContextVar[UserContext | None] = contextvars.ContextVar("skill_agent_user", default=None)
