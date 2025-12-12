import html
import aiogram.types as agtypes

from .const import MsgType


def make_short_user_info(user: agtypes.User | None=None, tguser=None) -> str:
    """
    Short text representation of a user
    """
    if user:
        user_id = user.id
    elif tguser:
        user_id = tguser.user_id
        user = tguser

    fullname = html.escape(user.full_name or '')
    tech_part = f'@{user.username}, id {user_id}' if user.username else f'id {user_id}'
    return f'{fullname} ({tech_part})'


def determine_msg_type(msg: agtypes.Message) -> str:
    """
    Determine a type of the message by inspecting its content
    """
    if msg.photo:
        return MsgType.photo
    elif msg.video:
        return MsgType.video
    elif msg.animation:
        return MsgType.animation
    elif msg.sticker:
        return MsgType.sticker
    elif msg.audio:
        return MsgType.audio
    elif msg.voice:
        return MsgType.voice
    elif msg.document:
        return MsgType.document
    elif msg.video_note:
        return MsgType.video_note
    elif msg.contact:
        return MsgType.contact
    elif msg.location:
        return MsgType.location
    elif msg.venue:
        return MsgType.venue
    elif msg.poll:
        return MsgType.poll
    elif msg.dice:
        return MsgType.dice
    else:
        return MsgType.regular_or_other