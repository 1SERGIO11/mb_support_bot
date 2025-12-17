"""
A package for system messages:
technical informing in chats, writing logs
"""
import datetime

import aiogram.types as agtypes
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from .enums import ActionName
from .gsheets import gsheets_save_admin_message, gsheets_save_user_message
from .common_utils import make_short_user_info


def log(func):
    """
    Decorator. Logs actions
    """
    async def wrapper(msg: agtypes.Message, *args, **kwargs):
        await msg.bot.log(func.__name__)
        return await func(msg, *args, **kwargs)

    wrapper.__name__ = func.__name__
    return wrapper


def handle_error(func):
    """
    Decorator. Processes any exception in a handler
    """
    async def wrapper(msg: agtypes.Message, *args, **kwargs):
        try:
            return await func(msg, *args, **kwargs)
        except TelegramForbiddenError:
            await report_user_ban(msg, func)
        except TelegramBadRequest as exc:
            if 'not enough rights to create a topic' in exc.message:
                await report_cant_create_topic(msg)
        except Exception as exc:
            await msg.bot.log_error(exc)

    wrapper.__name__ = func.__name__
    return wrapper


@log
async def report_user_ban(msg: agtypes.Message, func) -> None:
    """
    Report when the user banned the bot
    """
    bot = msg.bot
    thread_id = msg.message_thread_id

    if func.__name__ == 'admin_message' and await bot.db.tguser.get(thread_id=thread_id):
        group_id = bot.cfg['admin_group_id']
        await bot.send_message(
            group_id, 'The user banned the bot', message_thread_id=thread_id,
        )


@log
async def report_cant_create_topic(msg: agtypes.Message) -> None:
    """
    Report when the bot can't create a topic
    """
    user = msg.chat

    await msg.bot.send_message(
        msg.bot.cfg['admin_group_id'],
        (f'New user <b>{make_short_user_info(user=user)}</b> writes to the bot, '
         'but the bot has not enough rights to create a topic.\n\n️️️❗ '
         'Make the bot admin, and give it a "Manage topics" permission.'),
    )


async def save_admin_message(msg: agtypes.Message, tguser) -> None:
    """
    Entrypoint for all the mechanisms of saving messages sent by admin.
    There is only one currently: Google Sheets.
    """
    gsheets_cred_file = msg.bot.cfg.get('save_messages_gsheets_cred_file', None)
    gsheets_filename = msg.bot.cfg.get('save_messages_gsheets_filename', None)
    if gsheets_cred_file and gsheets_filename:
        await gsheets_save_admin_message(msg, tguser)


async def save_user_message(
        msg: agtypes.Message,
        new_user: bool = False,
        stat: bool = True,
    ) -> None:
    """
    Entrypoint for all the mechanisms of saving messages sent by user.
    There is only one currently: Google Sheets.
    """
    bot = msg.bot

    gsheets_cred_file = bot.cfg.get('save_messages_gsheets_cred_file', None)
    gsheets_filename = bot.cfg.get('save_messages_gsheets_filename', None)
    if gsheets_cred_file and gsheets_filename:
        await gsheets_save_user_message(msg, highlight=new_user)

    if stat:
        await bot.db.action.add(ActionName.user_message)
    if new_user:
        await bot.db.action.add(ActionName.new_user)


def _format_admin_rows(rows: list) -> str:
    if not rows:
        return '—'

    lines = []
    for _, name, replies, edits, deletes in rows:
        parts = [f"✉️ {replies or 0}"]
        if edits:
            parts.append(f"✏️ {edits}")
        if deletes:
            parts.append(f"🗑️ {deletes}")
        lines.append(f"• <b>{name}</b> — " + ', '.join(parts))
    return '\n'.join(lines)


async def build_stats_report(bot, from_date: datetime.date, to_date: datetime.date | None = None, title: str = '') -> str:
    to_date = to_date or datetime.date.today()
    header = f"<b>{title}</b> ({from_date} — {to_date})" if title else f"Статистика {from_date} — {to_date}"

    actions = await bot.db.action.get_grouped(from_date, to_date)
    action_map = {row[0]: row[1] for row in actions}

    admin_rows = await bot.db.adminstats.get_range(from_date, to_date)
    total_replies = sum(row[2] or 0 for row in admin_rows)
    total_edits = sum(row[3] or 0 for row in admin_rows)
    total_deletes = sum(row[4] or 0 for row in admin_rows)

    msg = [header]
    msg.append('\n<b>Пользователи</b>')
    msg.append(f"• Новых: {action_map.get(ActionName.new_user, 0) or 0}")
    msg.append(f"• Сообщений: {action_map.get(ActionName.user_message, 0) or 0}")

    msg.append('\n<b>Админы</b>')
    totals = [f"✉️ {total_replies}"]
    if total_edits:
        totals.append(f"✏️ {total_edits}")
    if total_deletes:
        totals.append(f"🗑️ {total_deletes}")
    msg.append('Всего: ' + ', '.join(totals))
    msg.append(_format_admin_rows(admin_rows))

    msg.append('\n<b>Системные метки</b>')
    msg.append('#stats')

    return '\n'.join(msg)


async def stats_to_admin_chat(bots: list, period: str = 'week') -> None:
    """Отправить статистику в закреплённый топик "Статистика" за нужный период.

    period: 'week' | 'month' | 'lifetime'
    """

    today = datetime.date.today()

    if period == 'month':
        from_date = today.replace(day=1)
        title = 'Статистика за месяц'
    elif period == 'lifetime':
        from_date = datetime.date(1970, 1, 1)
        title = 'Всего за всё время'
    else:  # week
        from_date = today - datetime.timedelta(days=6)
        title = 'Статистика за неделю'

    for bot in bots:
        thread_id = await bot.ensure_stats_topic()
        main_msg = await build_stats_report(bot, from_date, title=title)
        lifetime_msg = ''
        if period != 'lifetime':
            lifetime_msg = '\n\n' + await build_stats_report(bot, datetime.date(1970, 1, 1), title='Всего за всё время')

        await bot.send_message(
            bot.cfg['admin_group_id'],
            f"{main_msg}{lifetime_msg}",
            message_thread_id=thread_id,
        )
