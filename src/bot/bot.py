"""Сборка и настройка ``vkbottle``-бота.

Тонкий адаптер: превращает входящее сообщение в вызов :class:`BotCore`,
конвертирует клавиатуру и отправляет ответ (длинный текст дробится).
"""

from __future__ import annotations

import logging

from vkbottle.bot import Bot, BotLabeler, Message

from src.bot.core import BotCore
from src.bot.keyboards import to_keyboard
from src.services.schedule_service import split_message

logger = logging.getLogger(__name__)


def build_bot(token: str, core: BotCore) -> Bot:
    """Создаёт бота и регистрирует обработчик сообщений."""
    bot = Bot(token=token)
    labeler = BotLabeler()

    @labeler.message()
    async def handle_message(message: Message) -> None:
        try:
            reply = await core.handle(
                message.from_id, message.text, message.payload
            )
            keyboard = to_keyboard(reply.keyboard) if reply.keyboard else None
            chunks = split_message(reply.text)
            for index, chunk in enumerate(chunks):
                is_last = index == len(chunks) - 1
                await message.answer(
                    chunk, keyboard=keyboard if is_last else None
                )
        except Exception:  # noqa: BLE001 - не даём боту упасть
            logger.exception(
                "Не удалось обработать сообщение от %s", message.from_id
            )

    bot.labeler.load(labeler)
    return bot
