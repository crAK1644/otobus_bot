import asyncio
import hashlib
import json
import logging
from datetime import timedelta

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramAPIError
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
from aiogram.utils.payload import decode_payload, encode_payload

from iett_bot.config import Settings
from iett_bot.db import Store, SubscriptionLimit
from iett_bot.domain import Line, normalize_code, turkish_upper, utcnow
from iett_bot.i18n import local_time, split_text, tr
from iett_bot.iett import IettClient, SourceError
from iett_bot.schedule import Schedule
from iett_bot.worker import Heartbeats, Monitor

log = logging.getLogger(__name__)


class Flow(StatesGroup):
    search = State()
    schedule = State()


def start_payload(code: str) -> str:
    """Deep-link payloads allow only A-Z, a-z, 0-9, _ and -; other characters are encoded."""
    return code if code.isascii() else "t_" + encode_payload(code)


def code_from_payload(payload: str) -> str:
    """Inverse of start_payload; raises ValueError for anything that is not a line code."""
    return normalize_code(decode_payload(payload[2:]) if payload.startswith("t_") else payload)


def telegram_language(person) -> str:
    return "en" if person and (person.language_code or "").startswith("en") else "tr"


def terminal_id(name: str) -> str:
    """Short stable id for a terminal, so buttons survive a reordered terminal list."""
    return hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()[:6]


def hour_label(hour: int, times: tuple[str, ...], lang: str) -> str:
    """'07 · 07:10 07:50' for a few trips, '07 · 12 trips' for many, '07' without a timetable."""
    own = [t for t in times if int(t[:2]) == hour]
    if not own:
        return f"{hour:02d}"
    if len(own) > 3:
        return f"{hour:02d} · " + tr(lang, "trips", count=len(own))
    return f"{hour:02d} · " + " ".join(own)


def describe_departures(lang: str, picks: dict[str, list[int]]) -> str:
    if not picks:
        return tr(lang, "departures_all")
    parts = [
        (tr(lang, "any_direction") if key == "*" else key)
        + " "
        + ", ".join(f"{hour:02d}" for hour in hours)
        for key, hours in sorted(picks.items())
    ]
    return tr(lang, "departures_some", picks=" · ".join(parts))


class UserContext(BaseMiddleware):
    def __init__(self, store: Store):
        self.store = store

    async def __call__(self, handler, event, data):
        person = event.from_user
        if not person:
            return
        data["user"] = await self.store.ensure_user(person.id, telegram_language(person))
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
    root = Router(name="bot")
    # Sibling of the subscriptions router, so UserContext never runs for it: reading the privacy
    # notice must not create a stored user record.
    public = Router(name="public")
    public.message.filter(F.chat.type == "private")

    @public.message(Command("privacy"))
    async def privacy(message: Message) -> None:
        stored = await store.user(message.chat.id)
        language = stored["language"] if stored else telegram_language(message.from_user)
        await message.answer(
            tr(language, "privacy"), link_preview_options=LinkPreviewOptions(is_disabled=True)
        )

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
            text += "\n" + describe_departures(lang, json.loads(sub["departures"]))
            rows.append(
                [
                    (tr(lang, "check_line", code=code), f"check:{code}"),
                    (tr(lang, "hours", code=code), f"hours:{code}"),
                ]
            )
            rows.append(
                [
                    (tr(lang, "departures", code=code), f"dep:{code}"),
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
    async def start(
        message: Message, user: dict, state: FSMContext, command: CommandObject
    ) -> None:
        await state.clear()
        await message.answer(tr(user["language"], "welcome"), reply_markup=keyboard(languages()))
        try:
            code = code_from_payload(command.args) if command.args else None
        except ValueError:
            code = None  # A malformed link just gets the normal welcome.
        if code:
            await follow_line(message.bot, user, code, state)
        else:
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

    async def follow_line(bot: Bot, user: dict, code: str, state: FSMContext) -> None:
        lang = user["language"]
        try:
            line = await source.resolve(code)
            if line is None:
                await bot.send_message(user["chat_id"], tr(lang, "no_results"))
                return
            added = await store.subscribe(user["chat_id"], line, settings.max_subscriptions)
        except SourceError:
            await bot.send_message(user["chat_id"], tr(lang, "source_error"))
            return
        except ValueError as exc:
            key = "limit" if isinstance(exc, SubscriptionLimit) else "no_results"
            await bot.send_message(user["chat_id"], tr(lang, key, limit=settings.max_subscriptions))
            return
        await state.clear()
        confirmation = tr(lang, "subscribed" if added else "already_subscribed", code=line.code)
        try:
            share = f"https://t.me/{(await bot.me()).username}?start={start_payload(line.code)}"
            confirmation += "\n" + tr(lang, "share", url=share)
        except TelegramAPIError:
            log.warning("Could not read the bot username; sending the confirmation without a link")
        await bot.send_message(
            user["chat_id"],
            confirmation,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        text = await current_status(user, line.code, summary=True)
        await bot.send_message(
            user["chat_id"],
            text,
            reply_markup=keyboard(
                [[(tr(lang, "check_now"), f"check:{line.code}"), (tr(lang, "add"), "add")]]
            ),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    @router.callback_query(F.data.startswith("follow:"))
    async def follow(callback: CallbackQuery, user: dict, state: FSMContext) -> None:
        await callback.answer()
        await follow_line(callback.bot, user, callback.data.split(":", 1)[1], state)

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

    async def departure_state(user: dict, code: str):
        """(terminals, times per terminal, current picks), or None if the line is not followed."""
        for sub in await store.subscriptions(user["chat_id"]):
            if sub["line_code"] == code:
                break
        else:
            return None
        timetable = await source.timetable(Line(code, sub["name"]))
        terminals = list(timetable)
        if not terminals:
            ends = [turkish_upper(part.strip()) for part in sub["name"].split(" - ")]
            terminals = ends if len(ends) == 2 and all(ends) else ["*"]
        return terminals, timetable, json.loads(sub["departures"])

    def departure_markup(lang, code, terminals, timetable, picks, selected):
        """`selected` is a terminal name (or None)."""
        rows = [
            [
                (
                    ("▸ " if key == selected else "")
                    + (tr(lang, "any_direction") if key == "*" else key)
                    + (f" ({len(picks[key])})" if picks.get(key) else ""),
                    f"depdir:{code}:{terminal_id(key)}",
                )
            ]
            for key in terminals
        ]
        if selected is not None:
            key = selected
            times = timetable.get(key, ())
            hours = sorted({int(t[:2]) for t in times}) or list(range(24))
            chosen = picks.get(key, [])
            buttons = [
                (
                    ("✓ " if hour in chosen else "") + hour_label(hour, times, lang),
                    f"dephr:{code}:{terminal_id(key)}:{hour}",
                )
                for hour in hours
            ]
            rows += [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
        rows.append([(tr(lang, "all_departures"), f"depall:{code}")])
        return keyboard(rows)

    async def refresh_departures(callback, user, code, selected, picks=None):
        state = await departure_state(user, code)
        if state is None:
            await callback.bot.send_message(user["chat_id"], tr(user["language"], "not_subscribed"))
            return
        terminals, timetable, stored = state
        markup = departure_markup(
            user["language"],
            code,
            terminals,
            timetable,
            stored if picks is None else picks,
            selected,
        )
        try:
            await callback.message.edit_reply_markup(reply_markup=markup)
        except TelegramAPIError as exc:
            if "not modified" not in str(exc):
                await callback.bot.send_message(
                    user["chat_id"],
                    tr(user["language"], "departures_prompt", code=code),
                    reply_markup=markup,
                )

    @router.callback_query(F.data.startswith("dep:"))
    async def departures(callback: CallbackQuery, user: dict) -> None:
        await callback.answer()
        code = callback.data.split(":", 1)[1]
        state = await departure_state(user, code)
        if state is None:
            await callback.bot.send_message(user["chat_id"], tr(user["language"], "not_subscribed"))
            return
        terminals, timetable, picks = state
        await callback.bot.send_message(
            user["chat_id"],
            tr(user["language"], "departures_prompt", code=code),
            reply_markup=departure_markup(
                user["language"],
                code,
                terminals,
                timetable,
                picks,
                terminals[0] if len(terminals) == 1 else None,
            ),
        )

    def callback_fields(callback: CallbackQuery, count: int) -> list[str] | None:
        fields = callback.data.split(":")
        return fields[1:] if len(fields) == count + 1 else None

    @router.callback_query(F.data.startswith("depdir:"))
    async def departure_direction(callback: CallbackQuery, user: dict) -> None:
        fields = callback_fields(callback, 2)
        state = await departure_state(user, fields[0]) if fields else None
        key = state and next((t for t in state[0] if terminal_id(t) == fields[1]), None)
        if not key:
            await callback.answer(tr(user["language"], "stale_buttons"))
            return
        await callback.answer()
        await refresh_departures(callback, user, fields[0], key)

    @router.callback_query(F.data.startswith("dephr:"))
    async def departure_hour(callback: CallbackQuery, user: dict) -> None:
        fields = callback_fields(callback, 3)
        state = await departure_state(user, fields[0]) if fields else None
        key = state and next((t for t in state[0] if terminal_id(t) == fields[1]), None)
        if (
            not key
            or not (fields[2].isascii() and fields[2].isdigit())
            or not 0 <= int(fields[2]) <= 23
        ):
            await callback.answer(tr(user["language"], "stale_buttons"))
            return
        picks = await store.toggle_departure(user["chat_id"], fields[0], key, int(fields[2]))
        if picks is None:
            await callback.answer(tr(user["language"], "not_subscribed"))
            return
        await callback.answer()
        await refresh_departures(callback, user, fields[0], key, picks)

    @router.callback_query(F.data.startswith("depall:"))
    async def departure_reset(callback: CallbackQuery, user: dict) -> None:
        code = callback.data.split(":", 1)[1]
        if not await store.set_departures(user["chat_id"], code, {}):
            await callback.answer(tr(user["language"], "not_subscribed"))
            return
        await callback.answer(tr(user["language"], "departures_all"))
        await refresh_departures(callback, user, code, None, {})

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

    root.include_router(public)
    root.include_router(router)
    return root
