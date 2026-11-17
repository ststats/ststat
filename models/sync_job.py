from dataclasses import dataclass, field
from typing import Any


@dataclass
class JobResult:
    records_read: int = 0
    records_written: int = 0
    records_skipped: int = 0
    source_cursor: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
