import datetime
import html

import aiogram.types as agtypes
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .const import MenuMode, ButtonMode, MSG_TEXT_LIMIT
from .informing import handle_error, log


async def make_user_info(user: agtypes.User, bot=None, tguser=None) -> str:
    """
    Text representation of a user
    """
    name = f'<b>{html.escape(user.full_name)}</b>'
    username = f'@{user.username}' if user.username else 'No username'
    userid = f'<b>ID</b>: <code>{user.id}</code>'
    fields = [name, username, userid]

    if lang := getattr(user, 'language_code', None):
        fields.append(f'Language code: {lang}')
    if premium := getattr(user, 'is_premium', None):
        fields.append(f'Premium: {premium}')

    if bot:
        uinfo = await bot.get_chat(user.id)
        fields.append(f'<b>Bio</b>: {html.escape(uinfo.bio)}' if uinfo.bio else 'No bio')

        if uinfo.active_usernames and len(uinfo.active_usernames) > 1:
            fields.append(f'Active usernames: @{", @".join(uinfo.active_usernames)}')

    if tguser and tguser.subject:
        fields.append(f'<b>Subject</b>: {tguser.subject}')

    return '\n\n'.join(fields)


async def destruct_messages(bots: list) -> None:
    """
    Delete messages for users, if a bot is set up to do so
    """
    for bot in bots:
        destructed = 0

        for var in 'destruct_user_messages_for_user', 'destruct_bot_messages_for_user':
            if val := bot.cfg.get(var):
                error_reported = False
                by_bot = var == 'destruct_bot_messages_for_user'
                before = datetime.datetime.utcnow() - datetime.timedelta(hours=val)
                msgs = await bot.db.msgtodel.get_many(before, by_bot)

                for msg in msgs:
                    try:
                        await bot.delete_message(msg.chat_id, msg.msg_id)
                        destructed += 1
                    except Exception as exc:
                        if not error_reported:
                            await bot.log_error(exc)
                        error_reported = True

                await bot.db.msgtodel.remove(msgs)

        if destructed:
            await bot.log(f'Messages destructed: {destructed}')


async def save_for_destruction(msg, bot, chat_id=None):
    """
    Save msg id to destruct the msg later, if required
    """
    if not msg:
        return

    if chat_id:  # special case when there is no full msg object
        if bot.cfg.get('destruct_bot_messages_for_user'):
            await bot.db.msgtodel.add(msg, chat_id=chat_id)
        return

    var = 'destruct_user_messages_for_user'
    if msg.from_user.is_bot:
        var = 'destruct_bot_messages_for_user'

    if bot.cfg.get(var):
        await bot.db.msgtodel.add(msg)


async def _new_topic(msg: agtypes.Message, tguser=None) -> int:
    """
    Create a new topic for the user
    """
    group_id = msg.bot.cfg['admin_group_id']
    user, bot = msg.chat, msg.bot

    response = await bot.create_forum_topic(group_id, user.full_name)
    thread_id = response.message_thread_id

    text = await make_user_info(user, bot=bot, tguser=tguser)
    text += '\n\n<i>Replies to any bot message in this topic will be sent to the user</i>'

    await bot.send_message(group_id, text, message_thread_id=thread_id)
    await _send_quick_replies(bot, thread_id)
    return thread_id


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

async def _send_quick_replies(bot, thread_id: int) -> None:
    """
    Drop a quick-reply keyboard into the topic if configured
    """

    if not bot.admin_quick_replies:
        return

    text = '⚡ Быстрые ответы для оператора'
    await send_new_msg_with_keyboard(
        bot,
        bot.cfg['admin_group_id'],
        text,
        bot.admin_quick_replies,
        message_thread_id=thread_id,
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


def _create_button(content):
    """
    Button factory
    """
    if 'label' in content:
        return Button(content)


def _get_kb_builder(menu: dict, msgid: int, path: str='') -> InlineKeyboardBuilder:
    """
    Construct an InlineKeyboardBuilder object based on a given menu structure.
    Args:
        menu (dict): A dict with menu items to display.
        msgid (int): message_id to place into callback data.
        path (str, optional): A path to remember in callback data,
            to be able to find an answer for a menu item.
    """
    builder = InlineKeyboardBuilder()

    for key, val in menu.items():
        if btn := _create_button(val):
            cbd = CBD(path=path, code=key, msgid=msgid).pack()
            if menu.get('menumode') == MenuMode.row:
                builder.button(text=btn.content['label'], callback_data=cbd)
            else:
                builder.row(btn.as_inline(cbd))

    if path:  # build bottom row with navigation
        btns = []
        cbd = CBD(path='', code='', msgid=msgid).pack()
        btns.append(InlineKeyboardButton(text='🏠', callback_data=cbd))

        if '.' in path:
            spl = path.split('.')
            cbd = CBD(path='.'.join(spl[:-2]), code=spl[-2], msgid=msgid).pack()
            btns.append(InlineKeyboardButton(text='←', callback_data=cbd))

        builder.row(*btns)

    return builder


class CBD(CallbackData, prefix='_'):
    """
    Callback Data
    """
    path: str  # separated inside by '.'
    code: str  # button identifier after the path
    msgid: int  # id of a message with this button


class Button:
    """
    Wrapper over an inline keyboard button
    """
    def __init__(self, content):
        self.content = content
        self._recognize_mode()

        empty_answer_allowed = self.mode in (ButtonMode.link, ButtonMode.file)
        self.answer = _extract_answer(content, empty=empty_answer_allowed)

    def _recognize_mode(self) -> None:
        if 'link' in self.content:
            self.mode = ButtonMode.link
        elif 'file' in self.content:
            self.mode = ButtonMode.file
        elif any([isinstance(v, dict) and 'label' in v for v in self.content.values()]):
            self.mode = ButtonMode.menu
        elif 'subject' in self.content:
            self.mode = ButtonMode.subject
        elif 'answer' in self.content:
            self.mode = ButtonMode.answer

    def as_inline(self, callback_data : str | None=None) -> InlineKeyboardButton:
        if self.mode in (ButtonMode.file, ButtonMode.answer, ButtonMode.menu, ButtonMode.subject):
            return InlineKeyboardButton(text=self.content['label'], callback_data=callback_data)
        elif self.mode == ButtonMode.link:
            return InlineKeyboardButton(text=self.content['label'], url=self.content['link'])
        raise ValueError('Unexpected button mode')


def _extract_answer(menu: dict, empty: bool=False) -> str:
    answer = (menu.get('answer') or '')[:MSG_TEXT_LIMIT]
    if not empty:
        answer = answer or '👀'
    return answer
