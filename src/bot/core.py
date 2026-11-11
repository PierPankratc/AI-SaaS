"""Ядро диалога бота: маршрутизация кнопок и сценарий регистрации.

Не зависит от ``vkbottle``: принимает ``(vk_id, text, payload)`` и возвращает
:class:`Reply`. Это позволяет тестировать всю логику без сети и ВКонтакте.
Любые неожиданные ошибки не раскрываются пользователю (см. :meth:`BotCore.handle`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.api.schemas import EducationLevel
from src.bot import ui
from src.bot.drafts import DraftStore, OnboardingDraft
from src.bot.payloads import Action, Payload, decode_payload
from src.bot.validation import clamp_text, sanitize_teacher_query
from src.db.repository import UserProfile, UserRepository
from src.services.schedule_service import Role, ScheduleService

logger = logging.getLogger(__name__)

RATE_LIMITED = "⏳ Слишком много запросов. Подождите несколько секунд."
INVALID_INPUT = "Не понял запрос. Пожалуйста, пользуйтесь кнопками меню."
INVALID_TEACHER_QUERY = (
    "Введите фамилию буквами (например, «Адеева»).\n"
    "Допустимы буквы, пробел и дефис."
)
MAIN_MENU_HINT = "Выберите действие 👇"
TEACHER_PROMPT = (
    "Введите фамилию преподавателя сообщением.\n"
    "Например: «Адеева» или «Адеева Татьяна Николаевна»."
)

_ONBOARDING_ACTIONS = frozenset(
    {
        Action.ROLE,
        Action.FACULTY,
        Action.LEVEL,
        Action.COURSE,
        Action.GROUP,
        Action.TEACHER,
        Action.PAGE,
    }
)


@dataclass(frozen=True)
class Reply:
    """Готовый ответ: текст и (опционально) клавиатура."""

    text: str
    keyboard: ui.KeyboardSpec | None = None


class BotCore:
    """Маршрутизация действий пользователя и сценарий регистрации."""

    def __init__(
        self,
        catalog: Any,
        schedules: ScheduleService,
        repository: UserRepository,
        drafts: DraftStore,
        rate_limiter: Any | None = None,
    ) -> None:
        self._catalog = catalog
        self._schedules = schedules
        self._repository = repository
        self._drafts = drafts
        self._rate_limiter = rate_limiter

    async def handle(
        self,
        vk_id: int,
        text: str | None,
        payload: Any,
    ) -> Reply:
        """Обрабатывает входящее событие и возвращает ответ."""
        try:
            return await self._handle(vk_id, text, payload)
        except Exception:  # noqa: BLE001 - детали ошибки не показываем
            logger.exception("Ошибка обработки сообщения vk_id=%s", vk_id)
            return Reply("⚠️ Произошла ошибка. Попробуйте позже.")

    async def _handle(self, vk_id: int, text: str | None, payload: Any) -> Reply:
        if self._rate_limiter is not None:
            if not await self._rate_limiter.allow(f"user:{vk_id}"):
                return Reply(RATE_LIMITED)

        decoded = decode_payload(payload)
        profile = await self._repository.get(vk_id)

        if decoded is None:
            if profile is not None:
                return Reply(MAIN_MENU_HINT, ui.main_menu_keyboard())
            draft = await self._drafts.get(vk_id)
            if draft is not None and draft.role == Role.TEACHER.value:
                return await self._handle_teacher_query(vk_id, text or "")
            return await self._start_onboarding(vk_id)

        action = decoded.action
        if profile is not None and action in _ONBOARDING_ACTIONS:
            return Reply(MAIN_MENU_HINT, ui.main_menu_keyboard())

        if action is Action.ROLE:
            return await self._on_role(vk_id, decoded)
        if action is Action.FACULTY:
            return await self._on_faculty(vk_id, decoded)
        if action is Action.LEVEL:
            return await self._on_level(vk_id, decoded)
        if action is Action.COURSE:
            return await self._on_course(vk_id, decoded)
        if action is Action.GROUP:
            return await self._on_group(vk_id, decoded)
        if action is Action.TEACHER:
            return await self._on_teacher(vk_id, decoded)
        if action is Action.PAGE:
            return await self._on_page(vk_id, decoded)
        if action is Action.TODAY:
            return await self._on_schedule(vk_id, profile, "today")
        if action is Action.TOMORROW:
            return await self._on_schedule(vk_id, profile, "tomorrow")
        if action is Action.WEEK:
            return await self._on_schedule(vk_id, profile, "week")
        if action is Action.CHANGE:
            return await self._on_change(vk_id)
        if action is Action.MENU:
            return await self._on_menu(vk_id, profile)
        return Reply(INVALID_INPUT)

    # --- сценарий регистрации ---

    async def _start_onboarding(self, vk_id: int) -> Reply:
        await self._drafts.clear(vk_id)
        await self._drafts.save(vk_id, OnboardingDraft())
        return Reply("Кто вы? Выберите роль 👇", ui.role_keyboard())

    async def _on_role(self, vk_id: int, payload: Payload) -> Reply:
        role = payload.value
        if role not in (Role.STUDENT.value, Role.TEACHER.value):
            return Reply(INVALID_INPUT)
        await self._drafts.save(vk_id, OnboardingDraft(role=str(role)))
        if role == Role.TEACHER.value:
            return Reply(TEACHER_PROMPT)
        return await self._show_faculties(vk_id, 0)

    async def _show_faculties(self, vk_id: int, page: int) -> Reply:
        faculties = await self._catalog.get_faculties()
        if not faculties:
            return Reply("Не удалось получить список институтов. Попробуйте позже.")
        return Reply(
            "Выберите институт 👇", ui.faculties_keyboard(faculties, page)
        )

    async def _on_faculty(self, vk_id: int, payload: Payload) -> Reply:
        faculty_id = _as_int(payload.value)
        draft = await self._drafts.get(vk_id)
        if faculty_id is None or draft is None:
            return await self._start_onboarding(vk_id)

        faculties = await self._catalog.get_faculties()
        faculty = next(
            (item for item in faculties if item.id == faculty_id), None
        )
        if faculty is None:
            return Reply(INVALID_INPUT)

        draft = draft.model_copy(
            update={
                "faculty_id": faculty.id,
                "faculty_name": faculty.name,
                "level": None,
                "course": None,
                "page": 0,
            }
        )
        await self._drafts.save(vk_id, draft)

        levels = await self._catalog.get_levels(faculty.id)
        if not levels:
            return Reply("Для этого института нет учебных групп.", ui.role_keyboard())
        return Reply(
            f"{faculty.name}\nВыберите ступень обучения 👇",
            ui.levels_keyboard(levels),
        )

    async def _on_level(self, vk_id: int, payload: Payload) -> Reply:
        draft = await self._drafts.get(vk_id)
        if draft is None or draft.faculty_id is None:
            return await self._start_onboarding(vk_id)

        try:
            level = EducationLevel(str(payload.value))
        except ValueError:
            return Reply(INVALID_INPUT)

        draft = draft.model_copy(
            update={"level": level.value, "course": None, "page": 0}
        )
        await self._drafts.save(vk_id, draft)

        courses = await self._catalog.get_courses(draft.faculty_id, level)
        if not courses:
            return Reply("Курсы не найдены.")
        return Reply(
            f"{draft.faculty_name} · {level.title}\nВыберите курс 👇",
            ui.courses_keyboard(courses),
        )

    async def _on_course(self, vk_id: int, payload: Payload) -> Reply:
        draft = await self._drafts.get(vk_id)
        course = _as_int(payload.value)
        if (
            draft is None
            or draft.faculty_id is None
            or draft.level is None
            or course is None
        ):
            return await self._start_onboarding(vk_id)

        level = EducationLevel(draft.level)
        draft = draft.model_copy(update={"course": course, "page": 0})
        await self._drafts.save(vk_id, draft)

        groups = await self._catalog.get_groups_for(
            draft.faculty_id, level, course
        )
        if not groups:
            return Reply("Группы не найдены.")
        return Reply(
            f"{draft.faculty_name} · {level.title} · {course} курс\n"
            "Выберите группу 👇",
            ui.groups_keyboard(groups, 0),
        )

    async def _on_group(self, vk_id: int, payload: Payload) -> Reply:
        draft = await self._drafts.get(vk_id)
        group_id = _as_int(payload.value)
        if (
            draft is None
            or draft.faculty_id is None
            or draft.level is None
            or draft.course is None
            or group_id is None
        ):
            return await self._start_onboarding(vk_id)

        level = EducationLevel(draft.level)
        groups = await self._catalog.get_groups_for(
            draft.faculty_id, level, draft.course
        )
        group = next((item for item in groups if item.id == group_id), None)
        if group is None:
            return Reply(INVALID_INPUT)

        profile = UserProfile(
            vk_id=vk_id,
            role=Role.STUDENT.value,
            faculty_id=draft.faculty_id,
            faculty_name=draft.faculty_name,
            edu_level=level.value,
            course=draft.course,
            group_id=group.id,
            group_name=group.name,
        )
        await self._repository.save(profile)
        await self._drafts.clear(vk_id)
        return Reply(
            f"✅ Готово! Ваша группа: {group.name}.\n{MAIN_MENU_HINT}",
            ui.main_menu_keyboard(),
        )

    async def _on_teacher(self, vk_id: int, payload: Payload) -> Reply:
        draft = await self._drafts.get(vk_id)
        teacher_id = _as_int(payload.value)
        if (
            draft is None
            or draft.role != Role.TEACHER.value
            or teacher_id is None
        ):
            return await self._start_onboarding(vk_id)

        teachers = await self._catalog.get_teachers()
        teacher = next(
            (item for item in teachers if item.id == teacher_id), None
        )
        if teacher is None:
            return Reply(INVALID_INPUT)

        profile = UserProfile(
            vk_id=vk_id,
            role=Role.TEACHER.value,
            teacher_id=teacher.id,
            teacher_name=teacher.name,
        )
        await self._repository.save(profile)
        await self._drafts.clear(vk_id)
        return Reply(
            f"✅ Готово! {teacher.name}.\n{MAIN_MENU_HINT}",
            ui.main_menu_keyboard(),
        )

    async def _handle_teacher_query(self, vk_id: int, text: str) -> Reply:
        query = sanitize_teacher_query(clamp_text(text, 100))
        if query is None:
            return Reply(INVALID_TEACHER_QUERY)

        draft = await self._drafts.get(vk_id)
        if draft is None or draft.role != Role.TEACHER.value:
            return await self._start_onboarding(vk_id)

        draft = draft.model_copy(update={"teacher_query": query, "page": 0})
        await self._drafts.save(vk_id, draft)
        return await self._show_teachers(query, 0)

    async def _show_teachers(self, query: str, page: int) -> Reply:
        teachers = await self._catalog.search_teachers(query)
        if not teachers:
            return Reply(
                f"По запросу «{query}» никого не нашёл. Введите другую фамилию."
            )
        return Reply(
            f"Найдено: {len(teachers)}. Выберите себя 👇",
            ui.teachers_keyboard(teachers, page),
        )

    async def _on_page(self, vk_id: int, payload: Payload) -> Reply:
        draft = await self._drafts.get(vk_id)
        if draft is None:
            return await self._start_onboarding(vk_id)

        page = payload.page or 0
        name = str(payload.value)
        if name == "faculties":
            return await self._show_faculties(vk_id, page)
        if name == "groups":
            if (
                draft.faculty_id is None
                or draft.level is None
                or draft.course is None
            ):
                return await self._start_onboarding(vk_id)
            groups = await self._catalog.get_groups_for(
                draft.faculty_id, EducationLevel(draft.level), draft.course
            )
            return Reply("Выберите группу 👇", ui.groups_keyboard(groups, page))
        if name == "teachers":
            if draft.teacher_query is None:
                return Reply(TEACHER_PROMPT)
            return await self._show_teachers(draft.teacher_query, page)
        return Reply(INVALID_INPUT)

    async def _on_schedule(
        self,
        vk_id: int,
        profile: UserProfile | None,
        kind: str,
    ) -> Reply:
        if profile is None:
            return await self._start_onboarding(vk_id)

        role = Role(profile.role)
        if role is Role.STUDENT:
            target = profile.group_id
            title = f"👨‍🎓 Группа {profile.group_name}"
        else:
            target = profile.teacher_id
            title = f"👨‍🏫 {profile.teacher_name}"
        if target is None:
            return await self._start_onboarding(vk_id)

        if kind == "today":
            text = await self._schedules.day_text(
                role, target, self._schedules.today(), title
            )
        elif kind == "tomorrow":
            text = await self._schedules.day_text(
                role, target, self._schedules.tomorrow(), title
            )
        else:
            text = await self._schedules.week_text(
                role, target, self._schedules.today(), title
            )
        return Reply(text, ui.main_menu_keyboard())

    async def _on_change(self, vk_id: int) -> Reply:
        await self._repository.reset(vk_id)
        await self._drafts.clear(vk_id)
        await self._drafts.save(vk_id, OnboardingDraft())
        return Reply(
            "Профиль сброшен. Кто вы? Выберите роль 👇", ui.role_keyboard()
        )

    async def _on_menu(self, vk_id: int, profile: UserProfile | None) -> Reply:
        if profile is None:
            return await self._start_onboarding(vk_id)
        return Reply(MAIN_MENU_HINT, ui.main_menu_keyboard())


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
