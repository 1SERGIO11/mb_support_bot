import datetime

import aiogram.types as agtypes
from aiogram import Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command

from . import buttons
from .admin_actions import BroadcastForm, admin_broadcast_ask_confirm, admin_broadcast_finish
from .buttons import admin_btn_handler, send_new_msg_with_keyboard, user_btn_handler
from .callback_data import CBD
from .informing import (
    build_stats_report,
    handle_error,
    log,
    save_admin_message,
    save_user_message,
)
from .filters import (
    ACommandFilter, BtnInAdminGroup, BtnInAdminTopic, BtnInPrivateChat, BotMention,
    InAdminGroup, InAdminTopic,
    GroupChatCreatedFilter, NewChatMembersFilter, PrivateChatFilter,
)
from .topics import create_user_topic
from .utils import save_for_destruction


@log
@handle_error
async def cmd_start(msg: agtypes.Message, *args, **kwargs) -> None:
    """
    Reply to /start
    """
    bot, user, db = msg.bot, msg.chat, msg.bot.db
    sentmsg = await send_new_msg_with_keyboard(bot, user.id, bot.cfg['hello_msg'], bot.menu)

    new_user = False
    if not await db.tguser.get(user=user):  # save user if it's new
        thread_id = await _new_topic(msg)
        await db.tguser.add(user, msg, thread_id)
        new_user = True

    await save_user_message(msg, new_user=new_user, stat=False)
    await save_for_destruction(msg, bot)
    await save_for_destruction(sentmsg, bot)


async def _group_hello(msg: agtypes.Message):
    """
    Send group hello message to a group
    """
    group = msg.chat

    text = f'Hello!\nID of this group: <code>{group.id}</code>'
    if not group.is_forum:
        text += '\n\n❗ Please enable topics in the group settings. This will also change its ID.'
    await msg.bot.send_message(group.id, text)


@log
@handle_error
async def added_to_group(msg: agtypes.Message, *args, **kwargs):
    """
    Report group ID when added to a group
    """
    for member in msg.new_chat_members:
        if member.id == msg.bot.id:
            await _group_hello(msg)
            break


@log
@handle_error
async def group_chat_created(msg: agtypes.Message, *args, **kwargs):
    """
    Report group ID when a group with the bot is created
    """
    await _group_hello(msg)


@log
@handle_error
async def user_message(msg: agtypes.Message, *args, **kwargs) -> None:
    """
    Create topic and a user row in db if needed,
    then forward user message to internal admin group
    """
    group_id = msg.bot.cfg['admin_group_id']
    bot, user, db = msg.bot, msg.chat, msg.bot.db

    tguser = await db.tguser.get(user=user)

    if tguser and getattr(tguser, 'banned', False):
        await save_user_message(msg, new_user=False, stat=False)
        await save_for_destruction(msg, bot)
        return

    can_message = bool(tguser and getattr(tguser, 'can_message', False))

    if not can_message:
        new_user = not bool(tguser)
        # User must tap "contact" first — show the hint and keep the menu visible
        gate_msg = bot.cfg.get('contact_gate_msg')
        sentmsg = await send_new_msg_with_keyboard(bot, user.id, gate_msg, bot.menu)

        if not tguser:
            tguser = await db.tguser.add(user, msg, first_replied=False, can_message=False)

        await save_user_message(msg, new_user=new_user, stat=False)
        await save_for_destruction(msg, bot)
        await save_for_destruction(sentmsg, bot)
        return

    if tguser:
        if thread_id := tguser.thread_id:
            try:
                await msg.forward(group_id, message_thread_id=thread_id)
            except TelegramBadRequest as exc:  # the topic vanished for whatever reason
                if 'thread not found' in exc.message.lower():
                    thread_id = await create_user_topic(msg, tguser=tguser)
                    await msg.forward(group_id, message_thread_id=thread_id)
        else:
            thread_id = await create_user_topic(msg, tguser=tguser)
            await msg.forward(group_id, message_thread_id=thread_id)

        if tguser.first_replied:
            await db.tguser.update(user.id, user_msg=msg, thread_id=thread_id)
        else:
            if bot.cfg['first_reply']:
                sentmsg = await bot.send_message(user.id, bot.cfg['first_reply'])
                await save_for_destruction(sentmsg, bot)
            await db.tguser.update(user.id, user_msg=msg, thread_id=thread_id, first_replied=True)

    else:
        thread_id = await create_user_topic(msg)
        if bot.cfg['first_reply']:
            sentmsg = await bot.send_message(user.id, bot.cfg['first_reply'])
            await save_for_destruction(sentmsg, bot)
        tguser = await db.tguser.add(user, msg, thread_id, first_replied=True, can_message=True)
        await msg.forward(group_id, message_thread_id=thread_id)

    await save_user_message(msg)
    await save_for_destruction(msg, bot)


@log
@handle_error
async def admin_message(msg: agtypes.Message, *args, **kwargs) -> None:
    """
    Forward an admin's message from a topic to the linked user without requiring a reply.

    Messages that start with "/" are treated as internal and ignored by the filter.
    """
    bot, db = msg.bot, msg.bot.db

    tguser = await db.tguser.get(thread_id=msg.message_thread_id)
    if not tguser:
        # Тред не привязан к пользователю (например, случайное сообщение в служебной теме).
        # Игнорируем без уведомлений, чтобы не шуметь операторам.
        return

    copied = await msg.copy_to(tguser.user_id)
    await db.adminstats.bump(msg.from_user.id, msg.from_user.full_name or msg.from_user.username, 'replies')
    await db.msgmirror.add(
        admin_chat_id=msg.chat.id,
        admin_msg_id=msg.message_id,
        user_chat_id=tguser.user_id,
        user_msg_id=copied.message_id,
        thread_id=msg.message_thread_id,
    )

    await save_admin_message(msg, tguser)
    await save_for_destruction(copied, bot, chat_id=tguser.user_id)



@log
@handle_error
async def mention_in_admin_group(msg: agtypes.Message, *args, **kwargs):
    """
    Report group ID when a group with the bot is created
    """
    bot, group = msg.bot, msg.chat

    await send_new_msg_with_keyboard(bot, group.id, 'Choose:', bot.admin_menu)


@log
@handle_error
async def admin_quick_reply_handler(call: agtypes.CallbackQuery, *args, **kwargs):
    """
    Handle quick-reply buttons inside admin topics
    """
    msg = call.message
    bot = msg.bot
    cbd = buttons.CBD.unpack(call.data)

    if not bot.admin_quick_replies:
        return await call.answer('Нет быстрых ответов в admin_replies.toml', show_alert=True)

    tguser = await bot.db.tguser.get(thread_id=msg.message_thread_id)
    if not tguser:
        return await call.answer('Не удалось найти пользователя для этой темы', show_alert=True)

    menuitem, _ = buttons._find_menu_item(bot.admin_quick_replies, cbd)
    btn = buttons._create_button(menuitem) if menuitem else None
    if not btn:
        return await call.answer('Ответ не найден', show_alert=True)

    sent_to_user = await bot.send_message(tguser.user_id, btn.answer)
    await msg.answer(f"➡️ Отправлено пользователю:\n{btn.answer}")

    await save_admin_message(sent_to_user, tguser)
    await save_for_destruction(sent_to_user, bot, chat_id=tguser.user_id)
    return await call.answer('Сообщение отправлено')


async def _mirror_update_from_admin(bot, admin_msg: agtypes.Message, mapping) -> bool:
    """Try to push the admin edit to the user; fallback to re-copy if needed.

    Returns True on success, False if nothing was updated.
    """

    try:
        if admin_msg.text is not None:
            await bot.edit_message_text(
                admin_msg.text,
                mapping.user_chat_id,
                mapping.user_msg_id,
                entities=admin_msg.entities,
                parse_mode=None,
            )
            return True
        if admin_msg.caption is not None:
            await bot.edit_message_caption(
                mapping.user_chat_id,
                mapping.user_msg_id,
                caption=admin_msg.caption,
                caption_entities=admin_msg.caption_entities,
                parse_mode=None,
            )
            return True
    except TelegramBadRequest:
        # Если сообщение у пользователя не редактируется (например, Telegram не даёт
        # изменить скопированное сообщение), заменим его новой копией и обновим
        # зеркальную связку, чтобы дальнейшие правки продолжали работать.
        try:
            new_copy = await admin_msg.copy_to(mapping.user_chat_id)
            await bot.db.msgmirror.add(
                admin_chat_id=mapping.admin_chat_id,
                admin_msg_id=mapping.admin_msg_id,
                user_chat_id=mapping.user_chat_id,
                user_msg_id=new_copy.message_id,
                thread_id=mapping.thread_id,
            )
            try:
                await bot.delete_message(mapping.user_chat_id, mapping.user_msg_id)
            except TelegramBadRequest:
                pass
            return True
        except TelegramBadRequest:
            return False

    return False


@log
@handle_error
async def admin_message_edit(msg: agtypes.Message, *args, **kwargs) -> None:
    """Mirror edits from admin topics to the user's chat."""

    bot, db = msg.bot, msg.bot.db
    mapping = await db.msgmirror.get(msg.chat.id, msg.message_id)
    if not mapping:
        return

    if await _mirror_update_from_admin(bot, msg, mapping):
        await db.adminstats.bump(msg.from_user.id, msg.from_user.full_name or msg.from_user.username, 'edits')


@log
@handle_error
async def admin_sync_message(msg: agtypes.Message, *args, **kwargs) -> None:
    """Force-sync a reply to the user if автозеркало не сработало (команда).

    Использование: ответьте на нужное сообщение и отправьте /sync или /resend.
    Бот попытается отредактировать копию у пользователя или пересоздать её.
    """

    if not msg.reply_to_message:
        return await msg.answer('Ответьте на сообщение, которое нужно синхронизировать')

    bot, db = msg.bot, msg.bot.db
    mapping = await db.msgmirror.get(msg.chat.id, msg.reply_to_message.message_id)
    if not mapping:
        return await msg.answer('Не нашёл связь с сообщением пользователя')

    updated = await _mirror_update_from_admin(bot, msg.reply_to_message, mapping)

    if updated:
        await msg.answer('✅ Сообщение обновлено у пользователя')
    else:
        await msg.answer('⚠️ Не удалось обновить сообщение у пользователя')


@log
@handle_error
async def admin_delete_message(msg: agtypes.Message, *args, **kwargs) -> None:
    """Delete admin message and its mirrored copy at the user side.

    Use as a reply inside a topic: /del or /delete as a command message.
    """

    if not msg.reply_to_message:
        return await msg.answer('Ответьте на сообщение, которое нужно удалить у пользователя')

    bot, db = msg.bot, msg.bot.db
    mapping = await db.msgmirror.get(msg.chat.id, msg.reply_to_message.message_id)
    if not mapping:
        return await msg.answer('Не нашёл, что удалить у пользователя для этого сообщения')

    try:
        await bot.delete_message(mapping.user_chat_id, mapping.user_msg_id)
    except TelegramBadRequest:
        pass

    try:
        await bot.delete_message(msg.chat.id, msg.reply_to_message.message_id)
    except TelegramBadRequest:
        pass

    await db.msgmirror.delete(msg.chat.id, msg.reply_to_message.message_id)
    await db.adminstats.bump(msg.from_user.id, msg.from_user.full_name or msg.from_user.username, 'deletes')

    # Clean up the /del command itself
    try:
        await bot.delete_message(msg.chat.id, msg.message_id)
    except TelegramBadRequest:
        pass


@log
@handle_error
async def admin_stats_command(msg: agtypes.Message, *args, **kwargs) -> None:
    """Показать статистику за период (день или неделя) в стат-туре."""

    bot = msg.bot
    text = msg.text or ''
    if 'today' in text:
        from_date = datetime.date.today()
        title = 'Статистика за сегодня'
    elif 'month' in text:
        from_date = datetime.date.today().replace(day=1)
        title = 'Статистика за месяц'
    else:
        from_date = datetime.date.today() - datetime.timedelta(days=6)
        title = 'Статистика за неделю'

    stats_thread = bot.cfg.get('stats_topic_id')
    if msg.message_thread_id and stats_thread and int(stats_thread) == msg.message_thread_id:
        thread_id = msg.message_thread_id
    else:
        thread_id = None
    report = await build_stats_report(bot, from_date, title=title)
    await bot.send_to_stats_topic(report, message_thread_id=thread_id)


@log
@handle_error
async def admin_ban_user(msg: agtypes.Message, *args, **kwargs) -> None:
    """Заблокировать пользователя в текущем топике (ответом на его сообщение)."""

    if not msg.reply_to_message:
        return await msg.answer('Ответьте на сообщение пользователя, которого нужно заблокировать')

    bot = msg.bot
    mapping = await bot.db.msgmirror.get(msg.chat.id, msg.reply_to_message.message_id)
    if not mapping:
        return await msg.answer('Не нашёл пользователя для этого сообщения')

    await bot.db.tguser.update(mapping.user_chat_id, banned=True)
    await msg.answer('🚫 Пользователь заблокирован, новые сообщения игнорируются')


@log
@handle_error
async def admin_unban_user(msg: agtypes.Message, *args, **kwargs) -> None:
    """Разблокировать пользователя в текущем топике (ответом на его сообщение)."""

    if not msg.reply_to_message:
        return await msg.answer('Ответьте на сообщение пользователя, которого нужно разблокировать')

    bot = msg.bot
    mapping = await bot.db.msgmirror.get(msg.chat.id, msg.reply_to_message.message_id)
    if not mapping:
        return await msg.answer('Не нашёл пользователя для этого сообщения')

    await bot.db.tguser.update(mapping.user_chat_id, banned=False)
    await msg.answer('✅ Пользователь разблокирован, сообщения снова будут приниматься')
@log
@handle_error
async def show_quick_replies(msg: agtypes.Message, *args, **kwargs):
    """
    Show quick replies in the current admin topic
    """
    bot = msg.bot

    if not bot.admin_quick_replies:
        return await msg.answer('⚠️ Быстрые ответы не настроены (admin_replies.toml)')

    text = '⚡ Быстрые ответы для оператора'
    await send_new_msg_with_keyboard(
        bot,
        msg.chat.id,
        text,
        bot.admin_quick_replies,
        message_thread_id=msg.message_thread_id,
    )


def register_handlers(dp: Dispatcher) -> None:
    """
    Register all the handlers to the provided dispatcher
    """
    # Commands first so /start не перехватывается как обычное сообщение
    dp.message.register(cmd_start, PrivateChatFilter(), Command('start'))

    # Пользователи теперь могут писать со слешами — это не мешает операторам
    dp.message.register(user_message, PrivateChatFilter())

    dp.message.register(admin_message, InAdminTopic(), ~ACommandFilter())
    dp.edited_message.register(admin_message_edit, InAdminTopic())
    dp.message.register(admin_sync_message, InAdminTopic(), Command('sync', 'resend'))
    dp.message.register(admin_delete_message, InAdminTopic(), Command('del', 'delete'))
    dp.message.register(admin_ban_user, InAdminTopic(), Command('ban'))
    dp.message.register(admin_unban_user, InAdminTopic(), Command('unban'))
    dp.message.register(admin_stats_command, InAdminGroup(), Command('stats', 'stats_week', 'stats_today', 'stats_month'))
    dp.message.register(admin_stats_command, InAdminTopic(), Command('stats', 'stats_week', 'stats_today', 'stats_month'))
    dp.message.register(show_quick_replies, InAdminTopic(), Command('quick'))

    dp.message.register(added_to_group, NewChatMembersFilter())
    dp.message.register(group_chat_created, GroupChatCreatedFilter())
    dp.message.register(mention_in_admin_group, BotMention(), InAdminGroup())

    dp.message.register(admin_broadcast_ask_confirm, BroadcastForm.message)
    dp.callback_query.register(admin_broadcast_finish, BroadcastForm.confirm, BtnInAdminGroup())

    dp.callback_query.register(admin_quick_reply_handler, BtnInAdminTopic())
    dp.callback_query.register(user_btn_handler, BtnInPrivateChat())
    dp.callback_query.register(admin_btn_handler, BtnInAdminGroup())
