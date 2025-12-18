"""
Display menu with buttons according to menu.toml file,
handle buttons actions
"""
from pathlib import Path

import aiogram.types as agtypes
import toml
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData

from .admin_actions import admin_broadcast_start, del_old_topics
from .const import MSG_TEXT_LIMIT, AdminBtn, ButtonMode, MenuMode
from .informing import handle_error, log
from .topics import create_user_topic
from .utils import save_for_destruction


def load_toml(path: Path) -> dict | None:
    """
    Read toml file
    """
    if path.is_file():
        with open(path) as f:
            return toml.load(f)


def _find_menu_item(menu: dict, cbd: CallbackData) -> [dict, str]:
    """
    Find a button info in bot menu tree by callback data.
    """
    target = menu
    pathlist = []
    for lvlcode in cbd.path.split('.'):
        if lvlcode:
            pathlist.append(lvlcode)
            target = target.get(lvlcode)

    pathlist.append(cbd.code)
    return target.get(cbd.code), '.'.join(pathlist)


@log
@handle_error
async def user_btn_handler(call: agtypes.CallbackQuery, *args, **kwargs):
    """
    A callback for any button shown to a user.
    """
    msg = call.message
    bot, chat = msg.bot, msg.chat
    cbd = CBD.unpack(call.data)
    menuitem, path = _find_menu_item(bot.menu, cbd)
    sentmsg = None
    unlocked_prompt = None

    if not cbd.path and not cbd.code:  # main menu
        sentmsg = await edit_or_send_new_msg_with_keyboard(bot, chat.id, cbd, bot.menu)

    elif btn := _create_button(menuitem):
        if btn.mode == ButtonMode.menu:
            sentmsg = await edit_or_send_new_msg_with_keyboard(bot, chat.id, cbd, menuitem, path)
        elif btn.mode == ButtonMode.file:
            sentmsg = await send_file(bot, chat.id, menuitem)
        elif btn.mode == ButtonMode.answer:
            unlocked_prompt = None
            if menuitem.get('start_chat'):
                tguser = await bot.db.tguser.get(user=chat)
                if tguser:
                    # Reset the flag so the user gets the first auto-reply again
                    await bot.db.tguser.update(chat.id, can_message=True, first_replied=False)
                else:
                    tguser = await bot.db.tguser.add(chat, msg, first_replied=False, can_message=True)

                unlocked_text = bot.cfg.get('contact_unlocked_msg')
                if unlocked_text:
                    unlocked_prompt = await msg.answer(unlocked_text)
            if menuitem.get('as_new_message'):
                sentmsg = await msg.answer(btn.answer)
            else:
                await bot.edit_message_text(
                    chat_id=chat.id,
                    message_id=msg.message_id,
                    text=btn.answer,
                    reply_markup=msg.reply_markup,
                    disable_web_page_preview=True,
                )
                sentmsg = None
        elif btn.mode == ButtonMode.subject:
            sentmsg = await set_subject(bot, chat, menuitem)

    await save_for_destruction(sentmsg, bot)
    await save_for_destruction(unlocked_prompt, bot)

    return await call.answer()


@log
@handle_error
async def admin_btn_handler(call: agtypes.CallbackQuery, *args, **kwargs):
    """
    A callback for any button shown in admin group.
    """
    cbd = CBD.unpack(call.data)

    if cbd.code == AdminBtn.del_old_topics:
        await del_old_topics(call)
    elif cbd.code == AdminBtn.broadcast:
        await admin_broadcast_start(call, kwargs['dispatcher'])

    return await call.answer()


async def send_file(bot, chat_id: int, menuitem: dict) -> agtypes.Message:
    """
    Shortcut for sending a file on a button press.
    """
    fpath = bot.botdir / 'files' / menuitem['file']
    if fpath.is_file():
        doc = agtypes.FSInputFile(fpath)
        caption = _extract_answer(menuitem, empty=True)
        return await bot.send_document(chat_id, document=doc, caption=caption)

    raise FileNotFoundError(fpath.resolve())


async def set_subject(bot, user: agtypes.User, menuitem: dict) -> agtypes.Message:
    """
    Set the chosen subject to the user and report that.
    """
    newsubj = menuitem['subject']
    group_id = bot.cfg['admin_group_id']

    answer = (menuitem.get('answer') or '')[:MSG_TEXT_LIMIT]
    answer = answer or f'Please write your question about "{menuitem["label"]}"'
    usrmsg = await bot.send_message(user.id, text=answer)

    if tguser := await bot.db.tguser.get(user=user):
        if tguser.thread_id and tguser.subject != newsubj:
            await bot.db.tguser.update(user.id, subject=newsubj)
            answer = 'The user changed subject to <b>' + newsubj + '</b>'
            await bot.send_message(group_id, answer, message_thread_id=tguser.thread_id)

    return usrmsg


async def edit_or_send_new_msg_with_keyboard(
        bot, chat_id: int, cbd: CallbackData, menu: dict, path: str='',
        message_thread_id: int | None = None) -> agtypes.Message:
    """
    Shortcut to edit a message, or,
    if it's not possible, send a new message.
    """
    text = _extract_answer(menu)
    try:
        markup = _get_kb_builder(menu, cbd.msgid, path).as_markup()
        return await bot.edit_message_text(chat_id=chat_id, message_id=cbd.msgid, text=text,
                                           reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest:
        return await send_new_msg_with_keyboard(
            bot, chat_id, text, menu, path, message_thread_id=message_thread_id,
        )


async def send_new_msg_with_keyboard(
        bot, chat_id: int, text: str, menu: dict | None, path: str='',
        message_thread_id: int | None = None) -> agtypes.Message:
    """
    Shortcut to send a message with a keyboard.
    """
    sentmsg = await bot.send_message(
        chat_id,
        text=text,
        disable_web_page_preview=True,
        message_thread_id=message_thread_id,
    )
    if menu:
        markup = _get_kb_builder(menu, sentmsg.message_id, path).as_markup()
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=sentmsg.message_id,
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    return sentmsg


def build_confirm_menu(yes_answer: str='Confirmed', no_answer: str='Canceled') -> dict:
    """
    Shortcut to build typical confirmation keyboard
    """
    menu = {
        'yes': {'label': '✅ Yes', 'answer': yes_answer},
        'no': {'label': '🚫 No', 'answer': no_answer},
        'menumode': MenuMode.row,
    }
    return menu
