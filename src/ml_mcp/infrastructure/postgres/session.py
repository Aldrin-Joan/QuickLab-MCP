"""Async database engine, session factory, and lifecycle management."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ml_mcp.config import Settings, get_settings

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages async PostgreSQL connection pool and session factories."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    def initialize(self, custom_url: str | None = None) -> None:
        """Initialize the async engine and sessionmaker."""
        db_url = custom_url or self.settings.database.url

        engine_kwargs: dict[str, Any] = {
            "echo": self.settings.database.echo,
            "pool_pre_ping": True,
        }

        # SQLite requires StaticPool for in-memory and does not support pool_size or max_overflow
        if db_url.startswith("sqlite"):
            from sqlalchemy.pool import StaticPool

            engine_kwargs["poolclass"] = StaticPool
            engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30.0}
        else:
            engine_kwargs.update(
                {
                    "pool_size": self.settings.database.pool_size,
                    "max_overflow": self.settings.database.max_overflow,
                    "pool_timeout": self.settings.database.pool_timeout,
                    "pool_recycle": self.settings.database.pool_recycle,
                }
            )

        self._engine = create_async_engine(db_url, **engine_kwargs)
        self._sessionmaker = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        logger.info(
            "Database engine initialized for %s", db_url.split("@")[-1] if "@" in db_url else db_url
        )

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self.initialize()
        assert self._engine is not None
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            self.initialize()
        assert self._sessionmaker is not None
        return self._sessionmaker

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession]:
        """Provide a transactional async session scope."""
        async with self.sessionmaker() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    async def check_health(self) -> bool:
        """Perform a quick healthcheck query (SELECT 1)."""
        try:
            async with self.session() as sess:
                result = await sess.execute(text("SELECT 1"))
                return result.scalar() == 1
        except Exception as exc:
            logger.error("Database healthcheck failed: %s", exc)
            return False

    async def close(self) -> None:
        """Dispose the underlying connection pool."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None
            logger.info("Database connection pool disposed")


_db_manager: DatabaseManager | None = None


def get_db_manager() -> DatabaseManager:
    """Return singleton DatabaseManager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager
