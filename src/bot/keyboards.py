"""Преобразование :class:`KeyboardSpec` в клавиатуру ``vkbottle``."""

from __future__ import annotations

from vkbottle import Keyboard, KeyboardButtonColor, Text

from src.bot.ui import KeyboardSpec

_COLORS = {
    "primary": KeyboardButtonColor.PRIMARY,
    "secondary": KeyboardButtonColor.SECONDARY,
    "positive": KeyboardButtonColor.POSITIVE,
    "negative": KeyboardButtonColor.NEGATIVE,
}


def to_keyboard(spec: KeyboardSpec) -> Keyboard:
    """Собирает объект ``Keyboard`` из описания."""
    keyboard = Keyboard(inline=spec.inline, one_time=False)
    for row_index, row in enumerate(spec.rows):
        if row_index > 0:
            keyboard.row()
        for button in row:
            keyboard.add(
                Text(button.text, payload=button.payload),
                color=_COLORS.get(button.color, KeyboardButtonColor.SECONDARY),
            )
    return keyboard
