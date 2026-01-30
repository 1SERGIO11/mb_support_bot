import enum


class ActionName(enum.Enum):
    new_user = 'new_user', 'New user'
    user_message = 'user_message', 'User message'
    admin_reply = 'admin_reply', 'Admin reply'
    admin_edit = 'admin_edit', 'Admin edit'
    admin_delete = 'admin_delete', 'Admin delete'
