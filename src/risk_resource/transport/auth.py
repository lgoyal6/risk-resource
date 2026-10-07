from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class Principal:
    user: str
    team: str
    role: str


def principal(x_api_key: str | None = Header(default=None)) -> Principal:
    config = json.loads(os.getenv("RR_API_KEYS", "{}"))
    if not config:
        return Principal("local", "demo", "approver")
    if not x_api_key:
        raise HTTPException(401, "API key required")
    digest = hashlib.sha256(x_api_key.encode()).hexdigest()
    for configured, value in config.items():
        if hmac.compare_digest(digest, configured):
            return Principal(value["user"], value["team"], value["role"])
    raise HTTPException(401, "invalid API key")


def require_role(p: Principal, *roles: str) -> Principal:
    order = {"viewer": 0, "planner": 1, "approver": 2}
    if order.get(p.role, -1) < max(order[r] for r in roles):
        raise HTTPException(403, "insufficient role")
    return p
