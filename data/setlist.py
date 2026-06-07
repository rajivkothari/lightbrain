"""
Setlist — ordered list of songs (with linked LightingPrograms) for a DJ performance.

Each SetlistEntry maps a song (identified by its SHA-256 fingerprint) to a saved
LightingProgram so LightBrain can auto-load the right lighting show when the DJ
transitions to the next track.

Usage:
    setlist = Setlist.create("Saturday Night")
    setlist.add_entry("Song A", fingerprint=fp_a, program_id=prog_a.program_id)
    setlist.add_entry("Song B", fingerprint=fp_b, program_id=prog_b.program_id)
    entry = setlist.find_by_fingerprint(fp_a)  # → SetlistEntry for Song A
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SetlistEntry:
    """One song slot in a Setlist."""
    entry_id:         str
    position:         int       # 1-indexed display order (auto-assigned)
    name:             str
    song_fingerprint: str       # SHA-256 for auto-matching
    song_file_path:   str = ""
    program_id:       str = ""  # linked LightingProgram (empty if none)
    transition_s:     float = 2.0   # blend-in duration before this entry
    notes:            str = ""


@dataclass
class SetlistSummary:
    """Lightweight summary for library listing (no full entry data)."""
    setlist_id:   str
    name:         str
    entry_count:  int
    created_at:   float
    notes:        str = ""


@dataclass
class Setlist:
    """
    An ordered list of songs with associated LightingPrograms.

    Entries are ordered by their `position` field (1-indexed).
    All mutating methods keep positions contiguous and bump `updated_at`.
    """
    setlist_id: str
    name:       str
    entries:    List[SetlistEntry]
    created_at: float
    updated_at: float
    notes:      str = ""

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @staticmethod
    def create(name: str, notes: str = "") -> "Setlist":
        now = time.time()
        return Setlist(
            setlist_id=str(uuid.uuid4()),
            name=name,
            entries=[],
            created_at=now,
            updated_at=now,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_entry(
        self,
        name:             str,
        song_fingerprint: str   = "",
        song_file_path:   str   = "",
        program_id:       str   = "",
        transition_s:     float = 2.0,
        notes:            str   = "",
    ) -> SetlistEntry:
        """Append a new entry at the end and return it."""
        entry = SetlistEntry(
            entry_id=        str(uuid.uuid4()),
            position=        len(self.entries) + 1,
            name=            name,
            song_fingerprint=song_fingerprint,
            song_file_path=  song_file_path,
            program_id=      program_id,
            transition_s=    transition_s,
            notes=           notes,
        )
        self.entries.append(entry)
        self.updated_at = time.time()
        return entry

    def remove_entry(self, entry_id: str) -> bool:
        """Remove an entry by ID. Returns True if found and removed."""
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.entry_id != entry_id]
        if len(self.entries) < before:
            self._renumber()
            self.updated_at = time.time()
            return True
        return False

    def move_entry(self, entry_id: str, new_position: int) -> bool:
        """
        Move an entry to a new 1-indexed position.
        Other entries shift to fill the gap. Returns True if found.
        """
        idx = next(
            (i for i, e in enumerate(self.entries) if e.entry_id == entry_id),
            None,
        )
        if idx is None:
            return False
        entry = self.entries.pop(idx)
        insert_at = max(0, min(new_position - 1, len(self.entries)))
        self.entries.insert(insert_at, entry)
        self._renumber()
        self.updated_at = time.time()
        return True

    def update_entry_program(self, entry_id: str, program_id: str) -> bool:
        """Link (or unlink) a LightingProgram to an entry. Returns True if found."""
        for entry in self.entries:
            if entry.entry_id == entry_id:
                entry.program_id = program_id
                self.updated_at  = time.time()
                return True
        return False

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def find_by_fingerprint(self, fingerprint: str) -> Optional[SetlistEntry]:
        """Return the first entry whose song_fingerprint matches, or None."""
        for entry in self.entries:
            if entry.song_fingerprint == fingerprint:
                return entry
        return None

    def entry_count(self) -> int:
        return len(self.entries)

    def all_fingerprints(self) -> List[str]:
        """Return the fingerprints of all entries (for fast index building)."""
        return [e.song_fingerprint for e in self.entries if e.song_fingerprint]

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def to_summary(self) -> SetlistSummary:
        return SetlistSummary(
            setlist_id=  self.setlist_id,
            name=        self.name,
            entry_count= len(self.entries),
            created_at=  self.created_at,
            notes=       self.notes,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _renumber(self) -> None:
        """Reassign 1-indexed positions after any structural change."""
        for i, entry in enumerate(self.entries):
            entry.position = i + 1
