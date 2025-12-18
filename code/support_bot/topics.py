import aiogram.types as agtypes

from .utils import make_user_info


async def create_user_topic(msg: agtypes.Message, tguser=None) -> int:
    """Create a fresh topic for the user and drop quick replies if configured."""

    bot = msg.bot
    group_id = bot.cfg['admin_group_id']
    user = msg.chat

    response = await bot.create_forum_topic(group_id, user.full_name)
    thread_id = response.message_thread_id

    text = await make_user_info(user, bot=bot, tguser=tguser)
    text += '\n\n<i>Replies to any bot message in this topic will be sent to the user</i>'

    await bot.send_message(group_id, text, message_thread_id=thread_id)

    if bot.admin_quick_replies:
        # Lazy import to avoid circular dependency on import time
        from .buttons import send_new_msg_with_keyboard

        await send_new_msg_with_keyboard(
            bot,
            bot.cfg['admin_group_id'],
            '⚡ Быстрые ответы для оператора',
            bot.admin_quick_replies,
            message_thread_id=thread_id,
        )

    return thread_id
