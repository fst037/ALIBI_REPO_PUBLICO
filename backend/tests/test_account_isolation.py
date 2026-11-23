"""Two accounts never reach each other's data: the scoping, on the real repositories.

Why this file does not go through the API like most of the suite. The suite runs on
the in-memory twin of the data layer (`ALIBI_DATABASE_URL` is empty), and that twin
is single-user by design: its functions accept an `owner_id` and ignore it
(`datasets_memory`: "the DB repository is where owner scoping actually happens").
Two "accounts" over the API therefore see the same data and prove nothing. The
scoping lives in the repositories as `WHERE owner_id = :caller` on every read and
every write, so that is what is exercised here: the *real* repository code, against
a real SQL engine (SQLite in memory), with two distinct users.

The only adaptation is the column type: the schema uses PostgreSQL's JSONB, which
SQLite cannot render, so JSONB is compiled as plain JSON for this engine. It changes
how a value is stored and nothing about which rows a query selects.

Ana creates a source, a folder, a conversation and a board. Bea, a different user,
then reaches for each by identifier and by listing. Every door must answer "not
found" or "empty", and Ana's data must be untouched afterwards.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlmodel import Session, SQLModel

from app.db.models import (
    ConversationSourceType,
    DatasetFileType,
    DatasetModel,
    DatasetStatus,
    User,
)
from app.repositories import conversations as conversations_repo
from app.repositories import dashboards as dashboards_repo
from app.repositories import datasets as datasets_repo
from app.repositories import folders as folders_repo


@compiles(JSONB, "sqlite")
def _jsonb_as_json(_type, _compiler, **_kw) -> str:
    return "JSON"


ANA, BEA = "usr_ana", "usr_bea"


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for user_id, email in ((ANA, "ana@acme.com"), (BEA, "bea@acme.com")):
            session.add(
                User(id=user_id, email=email, name=user_id, workspace="Personal", email_verified=True)
            )
        session.commit()
        yield session


@pytest.fixture
def anas_data(db: Session) -> dict[str, str]:
    """One of everything, all Ana's: a confirmed source, a folder holding it, a conversation and a board."""
    dataset = DatasetModel(
        id="ds_ana",
        owner_id=ANA,
        name="ventas",
        file_type=DatasetFileType.csv,
        status=DatasetStatus.processed,
        table_name="ventas",
    )
    db.add(dataset)
    db.commit()
    folder = folders_repo.create_folder(db, ANA, "Ventas de Ana", ["ds_ana"])
    conversation = conversations_repo.create_conversation(
        db, ANA, ConversationSourceType.folder, folder_id=folder.id
    )
    board = dashboards_repo.create_dashboard(db, ANA, "Tablero de Ana", folder.id, folder.name)
    db.commit()
    return {"dataset": "ds_ana", "folder": folder.id, "conversation": conversation.id, "board": board.id}


def test_the_premise_holds_ana_does_see_her_own_data(db: Session, anas_data: dict) -> None:
    """If this fails, every 'Bea sees nothing' below is a broken fixture and not isolation."""
    assert [d.id for d in datasets_repo.list_datasets(db, ANA)] == ["ds_ana"]
    assert datasets_repo.get_dataset(db, ANA, "ds_ana") is not None
    assert [f.id for f in folders_repo.list_folders(db, ANA)] == [anas_data["folder"]]
    assert conversations_repo.get_conversation(db, ANA, anas_data["conversation"]) is not None
    assert [b.id for b in dashboards_repo.list_dashboards(db, ANA)] == [anas_data["board"]]


class TestANewAccountStartsEmpty:
    """A new library is empty and never shows another account's data."""

    def test_bea_lists_nothing_of_anas(self, db: Session, anas_data: dict) -> None:
        assert datasets_repo.list_datasets(db, BEA) == []
        assert folders_repo.list_folders(db, BEA) == []
        assert conversations_repo.list_conversations(db, BEA, recent=False, limit=50) == []
        assert dashboards_repo.list_dashboards(db, BEA) == []


class TestNothingOfAnasIsReachableByIdentifier:
    """Knowing an identifier is not enough."""

    def test_bea_cannot_read_anas_source_folder_conversation_or_board(
        self, db: Session, anas_data: dict
    ) -> None:
        assert datasets_repo.get_dataset(db, BEA, anas_data["dataset"]) is None
        assert folders_repo.get_folder(db, BEA, anas_data["folder"]) is None
        assert not folders_repo.exists(db, BEA, anas_data["folder"])
        assert conversations_repo.get_conversation(db, BEA, anas_data["conversation"]) is None
        assert not conversations_repo.conversation_exists(db, BEA, anas_data["conversation"])
        assert dashboards_repo.get_dashboard(db, BEA, anas_data["board"]) is None

    def test_bea_cannot_change_or_delete_anas_data_and_ana_keeps_it(
        self, db: Session, anas_data: dict
    ) -> None:
        assert folders_repo.rename_folder(db, BEA, anas_data["folder"], "robada") is None
        assert folders_repo.delete_folder(db, BEA, anas_data["folder"]) is False
        assert conversations_repo.delete_conversation(db, BEA, anas_data["conversation"]) is False
        db.commit()

        folder = folders_repo.get_folder(db, ANA, anas_data["folder"])
        assert folder is not None and folder.name == "Ventas de Ana"
        assert conversations_repo.get_conversation(db, ANA, anas_data["conversation"]) is not None
        assert datasets_repo.get_dataset(db, ANA, anas_data["dataset"]) is not None

    def test_bea_cannot_put_anas_source_in_her_own_folder(self, db: Session, anas_data: dict) -> None:
        """Attaching a source that is not yours is silently skipped: the folder ends up empty."""
        folder = folders_repo.create_folder(db, BEA, "Carpeta de Bea", [anas_data["dataset"]])
        db.commit()
        detail = folders_repo.get_folder(db, BEA, folder.id)
        assert detail is not None
        assert [d.id for d in detail.datasets] == []
