"""
SetlistStore — JSON-backed persistence for Setlist instances.

Each setlist is stored as one file:
    {store_dir}/{setlist_id}.json

An index tracks summaries + per-setlist fingerprint lists for fast lookup:
    {store_dir}/setlist_index.json

Usage:
    store = SetlistStore("/path/to/setlists/")
    store.save(setlist)
    sl    = store.load(setlist_id)
    all_  = store.list_setlists()         # List[SetlistSummary]
    result = store.find_by_fingerprint(fp) # Optional[Tuple[Setlist, SetlistEntry]]
"""

import json
import os
import time
from dataclasses import asdict
from typing import List, Optional, Tuple

from data.setlist import Setlist, SetlistEntry, SetlistSummary


class SetlistStore:
    """JSON-backed store for Setlist instances."""

    def __init__(self, store_dir: str):
        self._dir = store_dir
        os.makedirs(store_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, setlist: Setlist) -> str:
        """Serialize and write setlist. Returns the file path."""
        setlist.updated_at = time.time()
        path = os.path.join(self._dir, f"{setlist.setlist_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_serialize(setlist), f, separators=(",", ":"))
        self._update_index(setlist)
        return path

    def load(self, setlist_id: str) -> Setlist:
        """Load by ID. Raises FileNotFoundError if not found."""
        path = os.path.join(self._dir, f"{setlist_id}.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return _deserialize(data)

    def delete(self, setlist_id: str) -> None:
        """Delete setlist. Silent if not found."""
        path = os.path.join(self._dir, f"{setlist_id}.json")
        if os.path.exists(path):
            os.remove(path)
        self._remove_from_index(setlist_id)

    def list_setlists(self) -> List[SetlistSummary]:
        """Return summaries of all setlists, sorted newest first."""
        index = self._load_index()
        summaries = []
        for item in index.values():
            try:
                summaries.append(SetlistSummary(
                    setlist_id=  item["setlist_id"],
                    name=        item["name"],
                    entry_count= item["entry_count"],
                    created_at=  item["created_at"],
                    notes=       item.get("notes", ""),
                ))
            except (KeyError, TypeError):
                pass
        return sorted(summaries, key=lambda s: s.created_at, reverse=True)

    def find_by_fingerprint(
        self,
        fingerprint: str,
    ) -> Optional[Tuple[Setlist, SetlistEntry]]:
        """
        Search all setlists for an entry matching the given fingerprint.

        Returns (Setlist, SetlistEntry) for the first match found (most recently
        created setlist wins), or None if no match exists.

        Uses the index's fingerprint list to avoid loading full setlist data
        unless a potential match is found.
        """
        index = self._load_index()
        # Sort candidates newest-first so the most recent setlist wins
        candidates = sorted(
            index.values(),
            key=lambda item: item.get("created_at", 0.0),
            reverse=True,
        )
        for item in candidates:
            if fingerprint in item.get("fingerprints", []):
                try:
                    setlist = self.load(item["setlist_id"])
                    entry   = setlist.find_by_fingerprint(fingerprint)
                    if entry:
                        return setlist, entry
                except (FileNotFoundError, KeyError):
                    pass
        return None

    def count(self) -> int:
        return len(self._load_index())

    # ------------------------------------------------------------------
    # Index helpers
    # ------------------------------------------------------------------

    def _index_path(self) -> str:
        return os.path.join(self._dir, "setlist_index.json")

    def _load_index(self) -> dict:
        path = self._index_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_index(self, index: dict) -> None:
        with open(self._index_path(), "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)

    def _update_index(self, setlist: Setlist) -> None:
        index = self._load_index()
        index[setlist.setlist_id] = {
            "setlist_id":  setlist.setlist_id,
            "name":        setlist.name,
            "entry_count": len(setlist.entries),
            "created_at":  setlist.created_at,
            "notes":       setlist.notes,
            "fingerprints": setlist.all_fingerprints(),
        }
        self._save_index(index)

    def _remove_from_index(self, setlist_id: str) -> None:
        index = self._load_index()
        index.pop(setlist_id, None)
        self._save_index(index)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize(setlist: Setlist) -> dict:
    return {
        "setlist_id": setlist.setlist_id,
        "name":       setlist.name,
        "entries": [
            {
                "entry_id":         e.entry_id,
                "position":         e.position,
                "name":             e.name,
                "song_fingerprint": e.song_fingerprint,
                "song_file_path":   e.song_file_path,
                "program_id":       e.program_id,
                "transition_s":     e.transition_s,
                "notes":            e.notes,
            }
            for e in setlist.entries
        ],
        "created_at": setlist.created_at,
        "updated_at": setlist.updated_at,
        "notes":      setlist.notes,
    }


def _deserialize(data: dict) -> Setlist:
    entries = [
        SetlistEntry(
            entry_id=        e["entry_id"],
            position=        e["position"],
            name=            e["name"],
            song_fingerprint=e.get("song_fingerprint", ""),
            song_file_path=  e.get("song_file_path",   ""),
            program_id=      e.get("program_id",       ""),
            transition_s=    e.get("transition_s",     2.0),
            notes=           e.get("notes",            ""),
        )
        for e in data.get("entries", [])
    ]
    return Setlist(
        setlist_id= data["setlist_id"],
        name=       data["name"],
        entries=    entries,
        created_at= data.get("created_at", 0.0),
        updated_at= data.get("updated_at", 0.0),
        notes=      data.get("notes", ""),
    )
