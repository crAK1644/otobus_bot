import asyncio
import logging
from datetime import timedelta

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from iett_bot.config import Settings
from iett_bot.db import Store, SubscriptionLimit
from iett_bot.domain import Line, normalize_code, utcnow
from iett_bot.i18n import local_time, split_text, tr
from iett_bot.iett import IettClient, SourceError
from iett_bot.schedule import Schedule
from iett_bot.worker import Heartbeats, Monitor

log = logging.getLogger(__name__)


class Flow(StatesGroup):
    search = State()
    schedule = State()


class UserContext(BaseMiddleware):
    def __init__(self, store: Store):
        self.store = store

    async def __call__(self, handler, event, data):
        person = event.from_user
        if not person:
            return
        language = "en" if (person.language_code or "").startswith("en") else "tr"
        data["user"] = await self.store.ensure_user(person.id, language)
        return await handler(event, data)


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=value) for label, value in row]
            for row in rows
        ]
    )


def languages() -> list[list[tuple[str, str]]]:
    return [[("Türkçe", "lang:tr"), ("English", "lang:en")]]


async def answer_parts(message: Message, text: str, markup=None) -> None:
    parts = split_text(text)
    for index, part in enumerate(parts):
        await message.answer(
            part,
            reply_markup=markup if index == len(parts) - 1 else None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )


def build_router(
    store: Store, source: IettClient, settings: Settings, monitor: Monitor | None = None
) -> Router:
    monitor = monitor or Monitor(store, source, settings, Heartbeats())
    router = Router(name="subscriptions")
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")
    router.message.middleware(UserContext(store))
    router.callback_query.middleware(UserContext(store))

    async def show_lines(message: Message, user: dict, with_languages: bool = False) -> None:
        lang = user["language"]
        subscriptions = await store.subscriptions(user["chat_id"])
        text = tr(lang, "settings" if with_languages else "lines")
        if not with_languages:
            text += "\n" + tr(lang, "multiple_lines", limit=settings.max_subscriptions)
        rows = languages() if with_languages else []
        if not subscriptions:
            text += "\n\n" + tr(lang, "no_lines")
        for sub in subscriptions:
            code = sub["line_code"]
            text += f"\n\n{code} — {sub['name']}\n{Schedule.from_json(sub['schedule']).describe()}"
            rows.append(
                [
                    (tr(lang, "check_line", code=code), f"check:{code}"),
                    (tr(lang, "hours", code=code), f"hours:{code}"),
                    (tr(lang, "remove", code=code), f"remove:{code}"),
                ]
            )
        rows.append([(tr(lang, "add"), "add")])
        await answer_parts(message, text, keyboard(rows))

    async def search(message: Message, query: str, user: dict) -> None:
        lang = user["language"]
        try:
            results = await source.search(query)
        except SourceError:
            await message.answer(tr(lang, "source_error"))
            return
        except ValueError:
            results = []
        if not results:
            await message.answer(tr(lang, "no_results"))
            return
        rows = [[(f"{line.code} — {line.name}"[:90], f"follow:{line.code}")] for line in results]
        await message.answer(tr(lang, "choose_result"), reply_markup=keyboard(rows))

    async def save_schedule(message: Message, code: str, value: str, user: dict) -> bool:
        lang = user["language"]
        try:
            code = normalize_code(code)
            schedule = Schedule.parse(value)
        except ValueError:
            await message.answer(tr(lang, "bad_schedule"))
            return False
        if not await store.set_schedule(user["chat_id"], code, schedule):
            await message.answer(tr(lang, "not_subscribed"))
            return False
        await message.answer(tr(lang, "schedule_saved", code=code, schedule=schedule.describe()))
        return True

    @router.message(CommandStart())
    async def start(message: Message, user: dict, state: FSMContext) -> None:
        await state.clear()
        await message.answer(tr(user["language"], "welcome"), reply_markup=keyboard(languages()))
        await show_lines(message, user)

    @router.message(Command("help"))
    async def help_command(message: Message, user: dict) -> None:
        await message.answer(tr(user["language"], "help"))

    @router.message(Command("cancel"))
    async def cancel(message: Message, user: dict, state: FSMContext) -> None:
        await state.clear()
        await message.answer(tr(user["language"], "cancelled"))

    @router.message(Command("lines"))
    async def lines_command(
        message: Message, user: dict, state: FSMContext, command: CommandObject
    ) -> None:
        await state.clear()
        if command.args:
            await search(message, command.args, user)
        else:
            await show_lines(message, user)

    @router.message(Command("settings"))
    async def settings_command(
        message: Message, user: dict, state: FSMContext, command: CommandObject
    ) -> None:
        await state.clear()
        if command.args and command.args.strip() in ("tr", "en"):
            lang = command.args.strip()
            await store.set_language(user["chat_id"], lang)
            await message.answer(tr(lang, "language_set"))
        elif command.args and len(command.args.split(maxsplit=1)) == 2:
            code, value = command.args.split(maxsplit=1)
            await save_schedule(message, code, value, user)
        else:
            await show_lines(message, user, with_languages=True)

    @router.message(Command("pause", "resume"))
    async def pause_resume(message: Message, user: dict, command: CommandObject) -> None:
        paused = command.command == "pause"
        await store.set_paused(user["chat_id"], paused)
        await message.answer(tr(user["language"], "paused" if paused else "resumed"))

    @router.message(Command("delete_me"))
    async def delete_prompt(message: Message, user: dict, state: FSMContext) -> None:
        await state.clear()
        lang = user["language"]
        await message.answer(
            tr(lang, "delete_prompt"),
            reply_markup=keyboard(
                [[(tr(lang, "delete_confirm"), "delete:yes"), (tr(lang, "cancel"), "delete:no")]]
            ),
        )

    async def current_status(user: dict, code: str | None = None, summary: bool = False) -> str:
        lang = user["language"]
        subs = await store.subscriptions(user["chat_id"])
        if not subs:
            return tr(lang, "no_lines")
        if code:
            try:
                code = normalize_code(code)
            except ValueError:
                return tr(lang, "not_subscribed")
            subs = [sub for sub in subs if sub["line_code"] == code]
            if not subs:
                return tr(lang, "not_subscribed")
        checks = dict(
            zip(
                [sub["line_code"] for sub in subs],
                await asyncio.gather(*(monitor.refresh(sub["line_code"]) for sub in subs)),
                strict=True,
            )
        )
        # Refresh timestamps after capturing and persisting the source responses.
        subs = [
            sub for sub in await store.subscriptions(user["chat_id"]) if sub["line_code"] in checks
        ]
        now = utcnow()
        text = tr(lang, "status_heading")
        if user["paused"]:
            text += "\n" + tr(lang, "paused_status")
        for sub in subs:
            line = Line(sub["line_code"], sub["name"])
            text += f"\n\n🚍 {line.code} — {line.name}\n"
            if not checks[line.code]:
                text += tr(lang, "refresh_failed") + "\n"
            last_check = sub["last_success_at"]
            if last_check:
                text += tr(lang, "last_check", time=local_time(last_check))
                if now - last_check > timedelta(seconds=settings.stale_after_seconds):
                    text += "\n" + tr(lang, "stale")
            else:
                text += tr(lang, "not_checked")
                text += "\n" + tr(lang, "stale")
            notices = await store.current_notices(line.code)
            if notices:
                text += "\n" + tr(lang, "notice_count", count=len(notices))
                if summary:
                    text += "\n" + tr(lang, "notice_delivery")
                else:
                    for notice in notices:
                        text += "\n\n" + notice["original_text"]
                        text += "\n" + tr(
                            lang, "detected", time=local_time(notice["first_seen_at"])
                        )
            if not notices and last_check:
                text += "\n" + tr(lang, "no_notices")
            text += "\n" + line.url
        return text

    @router.message(Command("status"))
    async def status(message: Message, user: dict, command: CommandObject) -> None:
        await message.answer(tr(user["language"], "checking"))
        await answer_parts(message, await current_status(user, command.args))

    @router.callback_query(F.data.startswith("check:"))
    async def check_line(callback: CallbackQuery, user: dict) -> None:
        await callback.answer(tr(user["language"], "checking"))
        text = await current_status(user, callback.data.split(":", 1)[1])
        for part in split_text(text):
            await callback.bot.send_message(
                user["chat_id"], part, link_preview_options=LinkPreviewOptions(is_disabled=True)
            )

    @router.callback_query(F.data.startswith("lang:"))
    async def language(callback: CallbackQuery, user: dict) -> None:
        lang = callback.data.split(":", 1)[1]
        if lang not in ("tr", "en"):
            await callback.answer()
            return
        await store.set_language(user["chat_id"], lang)
        await callback.answer(tr(lang, "language_set"))
        await callback.bot.send_message(user["chat_id"], tr(lang, "choose_line"))

    @router.callback_query(F.data == "add")
    async def add(callback: CallbackQuery, user: dict, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(Flow.search)
        await callback.bot.send_message(user["chat_id"], tr(user["language"], "choose_line"))

    @router.callback_query(F.data.startswith("follow:"))
    async def follow(callback: CallbackQuery, user: dict, state: FSMContext) -> None:
        await callback.answer()
        code = callback.data.split(":", 1)[1]
        lang = user["language"]
        try:
            line = await source.resolve(code)
            if line is None:
                await callback.bot.send_message(user["chat_id"], tr(lang, "no_results"))
                return
            added = await store.subscribe(user["chat_id"], line, settings.max_subscriptions)
        except SourceError:
            await callback.bot.send_message(user["chat_id"], tr(lang, "source_error"))
            return
        except ValueError as exc:
            key = "limit" if isinstance(exc, SubscriptionLimit) else "no_results"
            await callback.bot.send_message(
                user["chat_id"], tr(lang, key, limit=settings.max_subscriptions)
            )
            return
        await state.clear()
        await callback.bot.send_message(
            user["chat_id"],
            tr(lang, "subscribed" if added else "already_subscribed", code=line.code),
        )
        text = await current_status(user, line.code, summary=True)
        await callback.bot.send_message(
            user["chat_id"],
            text,
            reply_markup=keyboard(
                [[(tr(lang, "check_now"), f"check:{line.code}"), (tr(lang, "add"), "add")]]
            ),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    @router.callback_query(F.data.startswith("remove:"))
    async def remove(callback: CallbackQuery, user: dict) -> None:
        await callback.answer()
        code = callback.data.split(":", 1)[1]
        await store.unsubscribe(user["chat_id"], code)
        await callback.bot.send_message(user["chat_id"], tr(user["language"], "removed", code=code))

    @router.callback_query(F.data.startswith("hours:"))
    async def hours(callback: CallbackQuery, user: dict, state: FSMContext) -> None:
        await callback.answer()
        code = callback.data.split(":", 1)[1]
        subscriptions = await store.subscriptions(user["chat_id"])
        if code not in {sub["line_code"] for sub in subscriptions}:
            await callback.bot.send_message(user["chat_id"], tr(user["language"], "not_subscribed"))
            return
        await state.set_state(Flow.schedule)
        await state.update_data(line_code=code)
        await callback.bot.send_message(
            user["chat_id"], tr(user["language"], "schedule_prompt", code=code)
        )

    @router.callback_query(F.data.startswith("delete:"))
    async def delete(callback: CallbackQuery, user: dict, state: FSMContext) -> None:
        await callback.answer()
        await state.clear()
        if callback.data == "delete:yes":
            await store.delete_user(user["chat_id"])
            await callback.bot.send_message(user["chat_id"], tr(user["language"], "deleted"))
        else:
            await callback.bot.send_message(user["chat_id"], tr(user["language"], "cancelled"))

    @router.message(Flow.schedule, F.text, ~F.text.startswith("/"))
    async def schedule_reply(message: Message, user: dict, state: FSMContext) -> None:
        data = await state.get_data()
        if await save_schedule(message, data["line_code"], message.text, user):
            await state.clear()

    @router.message(F.text, ~F.text.startswith("/"))
    async def search_reply(message: Message, user: dict) -> None:
        await search(message, message.text, user)

    @router.message()
    async def fallback(message: Message, user: dict) -> None:
        await message.answer(tr(user["language"], "help"))

    @router.error()
    async def error(event: ErrorEvent) -> bool:
        log.error("Telegram update failed (%s)", type(event.exception).__name__)
        message = event.update.message
        if message and message.chat.type == "private":
            user = await store.user(message.chat.id)
            await message.answer(tr(user["language"] if user else "tr", "unexpected_error"))
        return True

    return router
