from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from iett_bot.i18n import tr


def build_redirect_router(username: str) -> Router:
    """Keep the former bot reachable without running a second monitor or changing user data."""
    router = Router(name="former-bot")
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")
    url = f"https://t.me/{username}"

    def response(person):
        lang = "en" if person and (person.language_code or "").startswith("en") else "tr"
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=tr(lang, "open_new_bot"), url=url)]]
        )
        return tr(lang, "bot_moved", url=url), markup

    @router.message()
    async def message(event: Message) -> None:
        text, markup = response(event.from_user)
        await event.answer(
            text, reply_markup=markup, link_preview_options=LinkPreviewOptions(is_disabled=True)
        )

    @router.callback_query()
    async def callback(event: CallbackQuery) -> None:
        await event.answer()
        text, markup = response(event.from_user)
        await event.bot.send_message(
            event.from_user.id,
            text,
            reply_markup=markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    return router
