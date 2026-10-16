import pandas as pd
import pytest

from cfbmodel import db
from cfbmodel.teams import TeamResolver, UnmatchedTeamsError, normalize


@pytest.fixture
def resolver(con, tmp_path):
    teams = pd.DataFrame([
        {"team_id": 1, "school": "Miami", "abbreviation": "MIA", "conference": "ACC", "classification": "fbs",
         "alternate_names": ["Miami (FL)"]},
        {"team_id": 2, "school": "Miami (OH)", "abbreviation": "M-OH", "conference": "MAC", "classification": "fbs",
         "alternate_names": []},
        {"team_id": 3, "school": "Texas A&M", "abbreviation": "TAMU", "conference": "SEC", "classification": "fbs",
         "alternate_names": []},
    ])
    db.upsert_df(con, "teams", teams, "team_id")
    aliases = tmp_path / "aliases.csv"
    aliases.write_text("source,source_name,team_id\nsp_plus,Miami-FL,1\nsp_plus,Miami-OH,2\n*,The U,1\n")
    return TeamResolver.from_db(con, aliases)


def test_normalize():
    assert normalize("Texas A&M") == normalize("texas a and m")
    assert normalize("Miami (OH)") == "miami oh"


def test_resolution_order(resolver):
    assert resolver.resolve("sp_plus", "Miami-FL") == 1
    assert resolver.resolve("sp_plus", "Miami-OH") == 2
    assert resolver.resolve("bcftoys", "The U") == 1          # wildcard alias
    assert resolver.resolve("bcftoys", "Texas A&M") == 3      # canonical
    assert resolver.resolve("bcftoys", "Miami (OH)") == 2


def test_unmatched_fails_loudly(resolver, tmp_path):
    assert resolver.resolve("bcftoys", "Miami Ohio") is None  # no alias or canonical match
    assert resolver.resolve("bcftoys", "Miami-FL") == 1       # matches alternate name "Miami (FL)"
    with pytest.raises(UnmatchedTeamsError) as e:
        resolver.raise_if_unmatched(tmp_path / "unmatched.csv")
    assert "Miami Ohio" in str(e.value)
    assert (tmp_path / "unmatched.csv").exists()
