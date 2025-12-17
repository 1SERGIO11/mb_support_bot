import datetime
from dataclasses import asdict, dataclass
from pathlib import Path

import aiogram.types as agtypes
import sqlalchemy as sa
from sqlalchemy.engine.row import Row as SaRow
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import false

from .enums import ActionName


Base = declarative_base()
BASE_DIR = Path(__file__).resolve().parent.parent


class TgUsers(Base):
    __tablename__ = 'tgusers'

    id = sa.Column(sa.Integer, primary_key=True, index=True)
    user_id = sa.Column(sa.Integer, index=True, nullable=False)
    full_name = sa.Column(sa.String(129))
    username = sa.Column(sa.String(32))
    thread_id = sa.Column(sa.Integer, index=True)
    last_user_msg_at = sa.Column(sa.DateTime)
    subject = sa.Column(sa.String(32))
    banned = sa.Column(sa.Boolean, default=False, nullable=False)
    first_replied = sa.Column(sa.Boolean, server_default=false(), nullable=False)
    can_message = sa.Column(sa.Boolean, server_default=false(), nullable=False)


class ActionStats(Base):
    __tablename__ = 'actionstats'

    id = sa.Column(sa.Integer, primary_key=True)
    name = sa.Column(sa.Enum(ActionName), nullable=False)
    date = sa.Column(sa.Date)
    count = sa.Column(sa.Integer, default=0)

    __table_args__ = (
        sa.UniqueConstraint('name', 'date'),
    )


class AdminStats(Base):
    __tablename__ = 'adminstats'

    id = sa.Column(sa.Integer, primary_key=True)
    admin_id = sa.Column(sa.Integer, nullable=False)
    admin_name = sa.Column(sa.String(128), nullable=False)
    date = sa.Column(sa.Date, nullable=False)
    replies = sa.Column(sa.Integer, default=0)
    edits = sa.Column(sa.Integer, default=0)
    deletes = sa.Column(sa.Integer, default=0)

    __table_args__ = (
        sa.UniqueConstraint('admin_id', 'date'),
    )


class MessagesToDelete(Base):
    __tablename__ = 'messages_to_delete'

    id = sa.Column(sa.Integer, primary_key=True)
    chat_id = sa.Column(sa.Integer, nullable=False)
    msg_id = sa.Column(sa.Integer, nullable=False)
    sent_at = sa.Column(sa.DateTime, nullable=False)
    by_bot = sa.Column(sa.Boolean, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint('chat_id', 'msg_id'),
    )


class MirroredMessages(Base):
    __tablename__ = 'mirrored_messages'

    id = sa.Column(sa.Integer, primary_key=True)
    admin_chat_id = sa.Column(sa.Integer, nullable=False)
    admin_msg_id = sa.Column(sa.Integer, nullable=False)
    user_chat_id = sa.Column(sa.Integer, nullable=False)
    user_msg_id = sa.Column(sa.Integer, nullable=False)
    thread_id = sa.Column(sa.Integer, nullable=True)

    __table_args__ = (
        sa.UniqueConstraint('admin_chat_id', 'admin_msg_id'),
    )


@dataclass
class DbTgUser:
    """
    Fake TgUser to return inserted TgUser row without another DB query
    """
    user_id: int
    full_name: str
    username: str
    thread_id: int
    last_user_msg_at: datetime.datetime
    subject: str = None
    banned: bool = False
    first_replied: bool = False  # whether first_reply has been sent or not
    can_message: bool = False


class SqlDb:
    """
    A database which uses SQL through SQLAlchemy.
    """
    def __init__(self, url: str):
        self.url = url
        self.tguser = SqlTgUser(url)
        self.action = SqlAction(url)
        self.msgtodel = SqlMessageToDelete(url)
        self.msgmirror = SqlMirroredMessages(url)
        self.adminstats = SqlAdminStats(url)


class SqlRepo:
    """
    Repository for a table
    """
    def __init__(self, url: str):
        self.url = url


class SqlTgUser(SqlRepo):
    """
    Repository for TgUsers table
    """

    def __init__(self, url: str):
        super().__init__(url)
        self._schema_checked = False

    async def _ensure_can_message_column(self) -> None:
        """Make sure older SQLite DBs have the can_message column.

        Some existing installs may not have run the migration yet; to avoid
        crashes on select/update, we lazily add the column on first access.
        """
        if self._schema_checked or 'sqlite' not in self.url:
            return

        async with create_async_engine(self.url).begin() as conn:
            result = await conn.execute(sa.text('PRAGMA table_info(tgusers)'))
            columns = {row[1] for row in result.fetchall()}
            if 'can_message' not in columns:
                await conn.execute(
                    sa.text(
                        "ALTER TABLE tgusers ADD COLUMN can_message "
                        "BOOLEAN NOT NULL DEFAULT 0"
                    )
                )

        self._schema_checked = True

    async def add(self,
                  user: agtypes.User,
                  user_msg: agtypes.Message,
                  thread_id: int | None = None,
                  first_replied: bool = False,
                  can_message: bool = False) -> DbTgUser:
        await self._ensure_can_message_column()
        tguser = DbTgUser(
            user_id=user.id, full_name=user.full_name, username=user.username, thread_id=thread_id,
            last_user_msg_at=user_msg.date.replace(tzinfo=None), first_replied=first_replied,
            can_message=can_message,
        )
        async with create_async_engine(self.url).begin() as conn:
            await conn.execute(sa.delete(TgUsers).filter_by(user_id=user.id))
            await conn.execute(sa.insert(TgUsers).values(**asdict(tguser)))

        return tguser

    async def get(self,
                  user: agtypes.User | None = None,
                  thread_id: int | None = None) -> SaRow | None:
        await self._ensure_can_message_column()
        if user:
            query = sa.select(TgUsers).where(TgUsers.user_id==user.id)
        else:
            query = sa.select(TgUsers).where(TgUsers.thread_id==thread_id)

        async with create_async_engine(self.url).begin() as conn:
            result = await conn.execute(query)
            if row := result.fetchone():
                return row

    async def update(self,
                     user_id: int,
                     user_msg: agtypes.Message | None = None,
                     **kwargs) -> None:
        """
        Update TgUser fields (thread_id, subject, etc) provided as kwargs.
        if user_msg provided, set it's date to last_user_msg_at field.
        """
        await self._ensure_can_message_column()
        if user_msg:
            kwargs['last_user_msg_at'] = user_msg.date.replace(tzinfo=None)

        async with create_async_engine(self.url).begin() as conn:
            await conn.execute(sa.update(TgUsers).where(TgUsers.user_id==user_id).values(**kwargs))

    async def del_thread_id(self, user_id: int) -> None:
        await self._ensure_can_message_column()
        async with create_async_engine(self.url).begin() as conn:
            query = sa.update(TgUsers).where(TgUsers.user_id==user_id).values(thread_id=None)
            await conn.execute(query)

    async def get_all(self) -> list[SaRow]:
        async with create_async_engine(self.url).begin() as conn:
            result = await conn.execute(sa.select(TgUsers))
            return result.fetchall()

    async def get_olds(self) -> list[SaRow]:
        async with create_async_engine(self.url).begin() as conn:
            ago = datetime.datetime.utcnow() - datetime.timedelta(weeks=2)
            query = sa.select(TgUsers).where(TgUsers.last_user_msg_at <= ago)

            result = await conn.execute(query)
            return result.fetchall()


class SqlAction(SqlRepo):
    """
    Repository for ActionStats table
    """
    async def add(self, name: str) -> None:
        """
        Sum it with the existing action count for today
        """
        async with create_async_engine(self.url).begin() as conn:
            vals = {'name': name, 'date': datetime.date.today(), 'count': 1}
            insert_q = sa.insert(ActionStats).values(vals)
            update_q = sa.update(ActionStats).values(count=ActionStats.count + 1).where(
                (ActionStats.name == vals['name']) & (ActionStats.date == vals['date'])
            )
            try:
                await conn.execute(insert_q)
            except IntegrityError:
                await conn.execute(update_q)

    async def get_grouped(self, from_date: datetime.date, to_date: datetime.date | None = None) -> list:
        """
        Statistics over time between from_date and to_date (inclusive)
        """
        to_date = to_date or datetime.date.today()
        async with create_async_engine(self.url).begin() as conn:
            query = (
                sa.select(ActionStats.name, sa.func.sum(ActionStats.count))
                .where(ActionStats.date >= from_date)
                .where(ActionStats.date <= to_date)
                .group_by(ActionStats.name)
            )
            result = await conn.execute(query)
            return result.fetchall()

    async def get_total(self) -> list:
        """
        Statistics over entire bot existence time
        """
        async with create_async_engine(self.url).begin() as conn:
            query = (
                sa.select(ActionStats.name, sa.func.sum(ActionStats.count))
                .group_by(ActionStats.name)
            )
            result = await conn.execute(query)
            return result.fetchall()


class SqlMessageToDelete(SqlRepo):
    """
    Repository for MessagesToDelete table
    """
    async def add(self, msg: agtypes.Message, chat_id: int | None = None) -> None:
        """
        Remember new message
        """
        if chat_id:  # special case when the message was copied
            vals = {'chat_id': chat_id, 'sent_at': datetime.datetime.utcnow(), 'by_bot': True}
        else:  # the usual full message object
            vals = {'chat_id': msg.chat.id, 'sent_at': msg.date, 'by_bot': msg.from_user.is_bot}

        vals['msg_id'] = msg.message_id

        async with create_async_engine(self.url).begin() as conn:
            try:
                await conn.execute(sa.insert(MessagesToDelete).values(vals))
            except IntegrityError:
                pass  # such message already in the db

    async def get_many(self, before: datetime.datetime, by_bot: bool) -> list[SaRow]:
        """
        Statistics over entire bot existence time
        """
        async with create_async_engine(self.url).begin() as conn:
            query = sa.select(MessagesToDelete).where(
                (MessagesToDelete.sent_at <= before) & (MessagesToDelete.by_bot == by_bot))
            result = await conn.execute(query)
            return result.fetchall()


class SqlMirroredMessages(SqlRepo):
    """Repository for mirrored admin→user messages."""

    def __init__(self, url: str):
        super().__init__(url)
        self._table_ready = False

    async def _ensure_table(self) -> None:
        if self._table_ready:
            return

        async with create_async_engine(self.url).begin() as conn:
            await conn.execute(
                sa.text(
                    """
                    CREATE TABLE IF NOT EXISTS mirrored_messages (
                        id INTEGER PRIMARY KEY,
                        admin_chat_id INTEGER NOT NULL,
                        admin_msg_id INTEGER NOT NULL,
                        user_chat_id INTEGER NOT NULL,
                        user_msg_id INTEGER NOT NULL,
                        thread_id INTEGER,
                        UNIQUE(admin_chat_id, admin_msg_id)
                    )
                    """
                )
            )
            await conn.execute(
                sa.text(
                    "CREATE INDEX IF NOT EXISTS idx_mirrors_user ON mirrored_messages(user_chat_id, user_msg_id)"
                )
            )

        self._table_ready = True

    async def add(
        self,
        admin_chat_id: int,
        admin_msg_id: int,
        user_chat_id: int,
        user_msg_id: int,
        thread_id: int | None,
    ) -> None:
        await self._ensure_table()
        async with create_async_engine(self.url).begin() as conn:
            await conn.execute(
                sa.text(
                    """
                    INSERT OR REPLACE INTO mirrored_messages
                    (admin_chat_id, admin_msg_id, user_chat_id, user_msg_id, thread_id)
                    VALUES (:admin_chat_id, :admin_msg_id, :user_chat_id, :user_msg_id, :thread_id)
                    """
                ),
                {
                    "admin_chat_id": admin_chat_id,
                    "admin_msg_id": admin_msg_id,
                    "user_chat_id": user_chat_id,
                    "user_msg_id": user_msg_id,
                    "thread_id": thread_id,
                },
            )

    async def get(self, admin_chat_id: int, admin_msg_id: int) -> sa.Row | None:
        await self._ensure_table()
        async with create_async_engine(self.url).begin() as conn:
            result = await conn.execute(
                sa.text(
                    "SELECT admin_chat_id, admin_msg_id, user_chat_id, user_msg_id, thread_id "
                    "FROM mirrored_messages WHERE admin_chat_id = :admin_chat_id AND admin_msg_id = :admin_msg_id"
                ),
                {"admin_chat_id": admin_chat_id, "admin_msg_id": admin_msg_id},
            )
            return result.fetchone()

    async def delete(self, admin_chat_id: int, admin_msg_id: int) -> None:
        await self._ensure_table()
        async with create_async_engine(self.url).begin() as conn:
            await conn.execute(
                sa.text(
                    "DELETE FROM mirrored_messages WHERE admin_chat_id = :admin_chat_id AND admin_msg_id = :admin_msg_id"
                ),
                {"admin_chat_id": admin_chat_id, "admin_msg_id": admin_msg_id},
            )

    async def remove(self, msgs: list[SaRow]) -> None:
        """
        Remove rows with these ids
        """
        if ids := [msg.id for msg in msgs]:
            async with create_async_engine(self.url).begin() as conn:
                query = sa.delete(MessagesToDelete).filter(MessagesToDelete.id.in_(ids))
                await conn.execute(query)


class SqlAdminStats(SqlRepo):
    """Repository for per-admin stats (replies/edits/deletes)."""

    def __init__(self, url: str):
        super().__init__(url)
        self._ensured = False

    async def _ensure_table(self) -> None:
        if self._ensured:
            return
        async with create_async_engine(self.url).begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self._ensured = True

    async def bump(self, admin_id: int, admin_name: str, field: str) -> None:
        await self._ensure_table()
        today = datetime.date.today()
        field = field.lower()
        if field not in {'replies', 'edits', 'deletes'}:
            return

        async with create_async_engine(self.url).begin() as conn:
            vals = {
                'admin_id': admin_id,
                'admin_name': admin_name or '—',
                'date': today,
                field: 1,
            }
            insert_q = sa.insert(AdminStats).values(vals)
            update_q = (
                sa.update(AdminStats)
                .where((AdminStats.admin_id == vals['admin_id']) & (AdminStats.date == vals['date']))
                .values(**{field: getattr(AdminStats, field) + 1, 'admin_name': vals['admin_name']})
            )
            try:
                await conn.execute(insert_q)
            except IntegrityError:
                await conn.execute(update_q)

    async def get_range(self, from_date: datetime.date, to_date: datetime.date | None = None) -> list[SaRow]:
        await self._ensure_table()
        to_date = to_date or datetime.date.today()
        async with create_async_engine(self.url).begin() as conn:
            query = (
                sa.select(
                    AdminStats.admin_id,
                    AdminStats.admin_name,
                    sa.func.sum(AdminStats.replies),
                    sa.func.sum(AdminStats.edits),
                    sa.func.sum(AdminStats.deletes),
                )
                .where(AdminStats.date >= from_date)
                .where(AdminStats.date <= to_date)
                .group_by(AdminStats.admin_id, AdminStats.admin_name)
                .order_by(sa.desc(sa.func.sum(AdminStats.replies)))
            )
            result = await conn.execute(query)
            return result.fetchall()
