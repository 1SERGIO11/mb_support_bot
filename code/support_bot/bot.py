import logging
import os
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from google.oauth2.service_account import Credentials

from .buttons import load_toml
from .const import AdminBtn
from .db import SqlDb


BASE_DIR = Path(__file__).resolve().parent.parent


class SupportBot(Bot):
    """
    Aiogram Bot Wrapper
    """
    cfg_vars = (
        'admin_group_id', 'hello_msg', 'first_reply', 'db_url', 'db_engine',
        'save_messages_gsheets_cred_file', 'save_messages_gsheets_filename', 'hello_ps',
        'destruct_user_messages_for_user', 'destruct_bot_messages_for_user', 'contact_gate_msg',
        'contact_unlocked_msg', 'stats_topic_id', 'stats_topic_name'
    )
    botdir_file_cfg_vars = ('save_messages_gsheets_cred_file',)

    def __init__(self, name: str, logger: logging.Logger):
        self.name = name
        self._logger = logger

        self.botdir.mkdir(parents=True, exist_ok=True)
        token, self.cfg = self._read_config()
        self._configure_db()
        self._load_menu()
        self._load_quick_replies()

        super().__init__(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    @property
    def botdir(self) -> Path:
        return BASE_DIR / '..' / 'shared' / self.name

    def _read_config(self) -> tuple[str, dict]:
        """
        Read a bot token and a config with other vars
        """
        cfg = {
            'name': self.name,
            'hello_msg': (
                'Здравствуйте! 👋\nВы в службе поддержки Sigma VPN.\n\n'
                '📌 Чем можем помочь:\n• Подключение и настройка\n• Медленная скорость или сайты не открываются\n'
                '• Тарифы, оплата, промокоды\n\n'
                'Чтобы написать оператору, нажмите «✉️ Написать оператору» в меню ниже — '
                'после нажатия откроется чат поддержки.'
            ),
            'first_reply': (
                '✅ Мы получили ваше сообщение и отвечаем как можно быстрее.\n'
                'Пожалуйста, не удаляйте чат, чтобы мы смогли прислать ответ.'
            ),
            'db_url': f'sqlite+aiosqlite:///{self.botdir}/db.sqlite',
            'db_engine': 'aiosqlite',
            'hello_ps': '\n\n<i>The bot is created by @moladzbel</i>',
            'contact_gate_msg': (
                '✉️ Чтобы обратиться в поддержку, нажмите кнопку «Написать оператору» в меню ниже. '
                'Сообщения попадут к оператору только после нажатия.'
            ),
            'contact_unlocked_msg': (
                '✉️ Чат поддержки открыт.\n\n'
                '<b>Опишите проблему одним сообщением и добавьте:</b>\n'
                '1. Вашу ОС\n'
                '2. Приложение для подключения\n'
                '3. Серверы, к которым пробовали подключиться\n'
                '4. Регион и оператора\n\n'
                'Это поможет быстрее разобраться и решить вашу проблему.'
            ),
            'stats_topic_name': 'Еженедельная статистика',
        }
        stats_file = self.botdir / 'stats_topic_id.txt'
        if stats_file.exists():
            cfg['stats_topic_id'] = stats_file.read_text().strip()

        for var in self.cfg_vars:
            envvar = os.getenv(f'{self.name}_{var.upper()}')
            if envvar not in (None, ''):
                cfg[var] = envvar

        # convert vars with filenames to actual pathes
        for var in self.botdir_file_cfg_vars:
            if var in cfg:
                cfg[var] = self.botdir / cfg[var]

        # validate and convert destruction vars
        for var in 'destruct_user_messages_for_user', 'destruct_bot_messages_for_user':
            if var in cfg:
                cfg[var] = int(cfg[var])
                if not 1 <= cfg[var] <= 47:
                    raise ValueError(f'{var} must be between 1 and 47 (hours)')

        if stats_topic_id := cfg.get('stats_topic_id'):
            cfg['stats_topic_id'] = int(stats_topic_id)

        cfg['hello_msg'] += cfg['hello_ps']
        return os.getenv(f'{self.name}_TOKEN'), cfg

    def _configure_db(self) -> None:
        if self.cfg['db_engine'] == 'aiosqlite':
            self.db = SqlDb(self.cfg['db_url'])

    async def log(self, message: str, level=logging.INFO) -> None:
        self._logger.log(level, f'{self.name}: {message}')

    async def log_error(self, exception: Exception, traceback: bool = True) -> None:
        self._logger.error(str(exception), exc_info=traceback)

    def get_gsheets_creds(self):
        """
        A callback to work with Google Sheets through gspread_asyncio.
        """
        cred_file = self.cfg.get('save_messages_gsheets_cred_file', None)
        creds = Credentials.from_service_account_file(cred_file)
        scoped = creds.with_scopes([
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ])
        return scoped

    def _load_menu(self) -> None:
        self.menu = load_toml(self.botdir / 'menu.toml')
        if self.menu:
            self.menu['answer'] = self.cfg['hello_msg']

        self.admin_menu = {
            AdminBtn.broadcast: {'label': '📢 Broadcast to all subscribers',
                                 'answer': ("Send here a message to broadcast, and then I'll ask "
                                            "for confirmation")},
            AdminBtn.del_old_topics: {'label': '🧹 Delete topics older than 2 weeks',
                                      'answer': ('Deleting topics older than 2 weeks...')},
        }

    def _load_quick_replies(self) -> None:
        """
        Load optional admin quick-reply scripts from admin_replies.toml
        """
        self.admin_quick_replies = load_toml(self.botdir / 'admin_replies.toml') or {}

    async def ensure_stats_topic(self) -> int:
        """Ensure a dedicated stats topic exists and persist its ID.

        If the ID is provided via env/file we reuse it. Otherwise, we create a
        new topic once and store its thread id under shared/{BOT}/stats_topic_id.txt
        for future runs.
        """

        if thread_id := self.cfg.get('stats_topic_id'):
            return int(thread_id)

        response = await self.create_forum_topic(
            self.cfg['admin_group_id'], self.cfg.get('stats_topic_name', 'Еженедельная статистика'),
        )
        thread_id = response.message_thread_id

        path = self.botdir / 'stats_topic_id.txt'
        path.write_text(str(thread_id))
        self.cfg['stats_topic_id'] = thread_id
        await self.log(f'Created stats topic {thread_id} and saved to {path}')
        return thread_id
