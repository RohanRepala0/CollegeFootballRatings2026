"""Team identity: every source name maps to a CFBD team_id (plan section 1).

Resolution order for a (source, name) pair:
  1. an explicit row in data/reference/team_aliases.csv for that source (or source '*')
  2. an exact match, after light normalization, on a CFBD school name,
     abbreviation or alternate name, provided it points at exactly one team

Anything else is unmatched. Unmatched names are collected and raised together,
loudly, with a CSV written for the owner to fill in.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

import duckdb

from . import config

ALIASES_CSV = config.REFERENCE_DIR / "team_aliases.csv"


def normalize(name: str) -> str:
    s = name.strip().lower()
    s = s.replace("&", " and ").replace("’", "'")
    s = re.sub(r"[().,'`]", " ", s)
    s = re.sub(r"[-/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class UnmatchedTeamsError(RuntimeError):
    def __init__(self, unmatched: dict[tuple[str, str], int], report_path: Path | None):
        self.unmatched = unmatched
        lines = [f"  [{src}] {name!r} (seen {n}x)" for (src, name), n in sorted(unmatched.items())]
        where = f"\nWrote {report_path}; add rows to {ALIASES_CSV}." if report_path else ""
        super().__init__(f"{len(unmatched)} unmatched team name(s):\n" + "\n".join(lines) + where)


class TeamResolver:
    def __init__(self, canonical: dict[str, set[int]], aliases: dict[tuple[str, str], int]):
        self.canonical = canonical
        self.aliases = aliases
        self.unmatched: dict[tuple[str, str], int] = defaultdict(int)

    @classmethod
    def from_db(cls, con: duckdb.DuckDBPyConnection, aliases_csv: Path | None = None) -> "TeamResolver":
        canonical: dict[str, set[int]] = defaultdict(set)
        rows = con.execute("SELECT team_id, school, abbreviation, alternate_names FROM teams").fetchall()
        for team_id, school, abbr, alts in rows:
            for n in [school, abbr, *(alts or [])]:
                if n:
                    canonical[normalize(n)].add(team_id)
        return cls(dict(canonical), load_aliases(aliases_csv or ALIASES_CSV))

    def resolve(self, source: str, name: str) -> int | None:
        name = name.strip()
        for key in ((source, normalize(name)), ("*", normalize(name))):
            if key in self.aliases:
                return self.aliases[key]
        ids = self.canonical.get(normalize(name), set())
        if len(ids) == 1:
            return next(iter(ids))
        self.unmatched[(source, name)] += 1
        return None

    def raise_if_unmatched(self, report_path: Path | None = None) -> None:
        if not self.unmatched:
            return
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open("w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["source", "source_name", "team_id", "times_seen"])
                for (src, name), n in sorted(self.unmatched.items()):
                    w.writerow([src, name, "", n])
        raise UnmatchedTeamsError(dict(self.unmatched), report_path)


def load_aliases(path: Path) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    if not path.exists():
        return out
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("team_id", "").strip():
                out[(row["source"].strip(), normalize(row["source_name"]))] = int(row["team_id"])
    return out


def sync_aliases_table(con: duckdb.DuckDBPyConnection, path: Path | None = None) -> int:
    """Mirror the CSV into the `team_aliases` table so SQL joins can use it."""
    path = path or ALIASES_CSV
    con.execute("DELETE FROM team_aliases")
    if not path.exists():
        return 0
    with path.open(newline="") as fh:
        rows = [
            (r["source"].strip(), r["source_name"].strip(), int(r["team_id"]))
            for r in csv.DictReader(fh)
            if r.get("team_id", "").strip()
        ]
    if rows:
        con.executemany("INSERT INTO team_aliases VALUES (?, ?, ?)", rows)
    return len(rows)
