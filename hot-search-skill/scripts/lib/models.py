from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SocialItem:
    item_id: str
    source: str
    title: str
    url: str
    published_at: str
    author: str = ""
    body: str = ""
    engagement: dict[str, int] = field(default_factory=dict)
    score: float = 0.0
    cluster_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceOutcome:
    source: str
    state: str
    items_returned: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
