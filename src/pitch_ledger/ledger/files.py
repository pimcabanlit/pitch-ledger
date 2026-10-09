"""Local file ledger: the in-memory ledger persisted as JSONL under ``<match_dir>/ledger/``.

Files: ``insights.jsonl``, ``audits.jsonl``, ``overlays.jsonl`` and ``triggers.jsonl``,
rewritten in full by ``flush()`` (a replay writes once at the end).
"""

from pathlib import Path

from pydantic import BaseModel

from pitch_ledger.ledger.memory import InMemoryLedger
from pitch_ledger.ledger.repository import IdKind
from pitch_ledger.schema import AuditRecord, Insight, Overlay, Trigger


class FileLedger(InMemoryLedger):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        for line in self._lines("triggers"):
            self.save_trigger(Trigger.model_validate_json(line))
        for line in self._lines("insights"):
            self.put_insight(Insight.model_validate_json(line))
        for line in self._lines("audits"):
            self.add_audit(AuditRecord.model_validate_json(line))
        for line in self._lines("overlays"):
            self.put_overlay(Overlay.model_validate_json(line))
        for insight in self.insights():
            self._seen(insight.insight_id, "I")
        for overlay in self.overlays():
            self._seen(overlay.overlay_id, "O")
        for audit in self.audits():
            self._seen(audit.audit_id, "A")

    def _seen(self, ident: str, kind: IdKind) -> None:
        """Keep ID counters ahead of IDs already on disk."""
        match_id, number = ident.split(f"-{kind}")
        self._counters[(match_id, kind)] = max(self._counters[(match_id, kind)], int(number))

    def _lines(self, name: str) -> list[str]:
        path = self.root / f"{name}.jsonl"
        return [x for x in path.read_text("utf-8").splitlines() if x] if path.exists() else []

    def flush(self) -> list[Path]:
        """Write every collection to disk; returns the paths written."""
        self.root.mkdir(parents=True, exist_ok=True)
        collections: dict[str, list[BaseModel]] = {
            "triggers": list(self.triggers.values()),
            "insights": list(self.insights()),
            "audits": list(self.audits()),
            "overlays": list(self.overlays()),
        }
        paths = []
        for name, rows in collections.items():
            path = self.root / f"{name}.jsonl"
            path.write_text("".join(r.model_dump_json() + "\n" for r in rows), "utf-8")
            paths.append(path)
        return paths
