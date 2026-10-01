"""The monitored sales team shared by the Jarvis web app and live worker.

The live worker must use the same list as the Jarvis interface: otherwise a
person added in the UI would be visible in filters but their recordings would
never be collected.  The database is the shared source of truth in production;
the built-in team keeps a new deployment and local development operational
until the settings table has been created.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SalesTeamMember:
    bitrix_user_id: int
    name: str


# Verified in Bitrix24 on 01.10.2026.  These entries are intentionally kept in
# code as the safe fallback if the database is temporarily unavailable.
DEFAULT_SALES_TEAM: tuple[SalesTeamMember, ...] = (
    SalesTeamMember(1286, "Роман Авсеенко"),
    SalesTeamMember(2100, "Ирина Богомольцева"),
    SalesTeamMember(2272, "Алена Хурсик"),
    SalesTeamMember(2274, "Ирина Базылева"),
)


class SalesTeamRepository:
    """Persistent monitored-team settings stored in the private Jarvis schema."""

    def __init__(self, connection: Any):
        self.connection = connection

    @classmethod
    def connect(cls, database_url: str) -> "SalesTeamRepository":
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("Install psycopg before managing the Jarvis team") from exc
        return cls(psycopg.connect(database_url, row_factory=dict_row))

    def close(self) -> None:
        self.connection.close()

    def ensure_schema(self) -> None:
        """Create settings storage only from the web settings screen.

        The live worker never runs DDL during a read.  Production normally gets
        this table from the migration; this small bootstrap makes the settings
        page usable immediately if the migration has not been applied yet.
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                create table if not exists jarvis.monitored_sales_team (
                  bitrix_user_id integer primary key,
                  display_name text not null,
                  active boolean not null default true,
                  added_at timestamptz not null default now(),
                  updated_at timestamptz not null default now()
                )
                """
            )
            self._seed_defaults(cursor)
        self.connection.commit()

    @staticmethod
    def _seed_defaults(cursor: Any) -> None:
        for member in DEFAULT_SALES_TEAM:
            cursor.execute(
                """
                insert into jarvis.monitored_sales_team
                  (bitrix_user_id, display_name, active)
                values (%s, %s, true)
                on conflict (bitrix_user_id) do nothing
                """,
                (member.bitrix_user_id, member.name),
            )

    def members(self, *, include_inactive: bool = False) -> list[SalesTeamMember]:
        query = "select bitrix_user_id, display_name from jarvis.monitored_sales_team"
        if not include_inactive:
            query += " where active = true"
        query += " order by display_name"
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(query)
                rows = cursor.fetchall()
            self.connection.commit()
        except Exception as exc:
            # A new worker must still analyse the core team before the settings
            # migration is installed.  Keep this fallback visible in logs.
            self.connection.rollback()
            logger.warning("Jarvis team settings unavailable (%s); using defaults", type(exc).__name__)
            return list(DEFAULT_SALES_TEAM)
        return [
            SalesTeamMember(int(row["bitrix_user_id"]), str(row["display_name"]))
            for row in rows
        ]

    def activate(self, member: SalesTeamMember) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                insert into jarvis.monitored_sales_team
                  (bitrix_user_id, display_name, active, updated_at)
                values (%s, %s, true, now())
                on conflict (bitrix_user_id) do update set
                  display_name = excluded.display_name,
                  active = true,
                  updated_at = now()
                """,
                (member.bitrix_user_id, member.name),
            )
        self.connection.commit()

    def deactivate(self, bitrix_user_id: int) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                update jarvis.monitored_sales_team
                set active = false, updated_at = now()
                where bitrix_user_id = %s
                """,
                (bitrix_user_id,),
            )
        self.connection.commit()


def active_sales_team(database_url: str | None = None) -> list[SalesTeamMember]:
    """Return the production team without allowing a read to change schema."""
    database_url = (database_url if database_url is not None else os.environ.get("JARVIS_DATABASE_URL", "")).strip()
    if not database_url:
        return list(DEFAULT_SALES_TEAM)
    try:
        repository = SalesTeamRepository.connect(database_url)
    except Exception as exc:
        logger.warning("Jarvis team database connection failed (%s); using defaults", type(exc).__name__)
        return list(DEFAULT_SALES_TEAM)
    try:
        members = repository.members()
        return members or list(DEFAULT_SALES_TEAM)
    finally:
        repository.close()


def ids(members: Iterable[SalesTeamMember]) -> set[int]:
    return {member.bitrix_user_id for member in members}


def names(members: Sequence[SalesTeamMember]) -> set[str]:
    return {member.name.strip().casefold() for member in members if member.name.strip()}
