from aiogram.filters.callback_data import CallbackData


class MenuCallbackData(CallbackData, prefix="_"):
    """Callback payload for bot menus and admin buttons."""

    path: str  # separated by '.'
    code: str  # button identifier after the path
    msgid: int  # id of a message with this button


# Backward-compatible alias
CBD = MenuCallbackData
