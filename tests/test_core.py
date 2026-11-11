"""Тесты ядра диалога (без vkbottle и сети)."""

from datetime import date

from src.api.eios_client import EiosApiError
from src.api.schemas import EducationLevel, Faculty, Group, Teacher
from src.bot.core import BotCore
from src.bot.drafts import InMemoryDrafts
from src.bot.payloads import Action, decode_payload, make_payload
from src.db.repository import UserProfile

FACULTIES = [Faculty(id=36, name="ИВИТШ"), Faculty(id=35, name="АСП")]
GROUPS = [
    Group(
        id=8948,
        name="26-ПМбо-1",
        course=1,
        faculty_id=36,
        faculty_name="ИВИТШ",
        education_level=EducationLevel.BACHELOR,
    )
]
TEACHERS = [Teacher(id=14, name="Адеева Татьяна Николаевна")]


class FakeCatalog:
    async def get_faculties(self):
        return FACULTIES

    async def get_levels(self, faculty_id: int):
        return [EducationLevel.BACHELOR] if faculty_id == 36 else []

    async def get_courses(self, faculty_id: int, level: EducationLevel):
        return [1] if faculty_id == 36 else []

    async def get_groups_for(
        self, faculty_id: int, level: EducationLevel, course: int
    ):
        return [
            group
            for group in GROUPS
            if group.faculty_id == faculty_id and group.course == course
        ]

    async def get_teachers(self):
        return TEACHERS

    async def search_teachers(self, query: str, limit=None):
        return [teacher for teacher in TEACHERS if query.lower() in teacher.name.lower()]


class FakeSchedules:
    def today(self) -> date:
        return date(2026, 11, 11)

    def tomorrow(self) -> date:
        return date(2026, 11, 12)

    async def day_text(self, role, target, day, title=None):
        return f"DAY {role.value} {target} {day.isoformat()} | {title}"

    async def week_text(self, role, target, day, title=None):
        return f"WEEK {role.value} {target} | {title}"


class FakeRepository:
    def __init__(self) -> None:
        self.data: dict[int, UserProfile] = {}

    async def get(self, vk_id: int):
        return self.data.get(vk_id)

    async def save(self, profile: UserProfile) -> None:
        self.data[profile.vk_id] = profile

    async def reset(self, vk_id: int) -> None:
        self.data.pop(vk_id, None)


class DenyLimiter:
    async def allow(self, key: str) -> bool:
        return False


def make_core(limiter=None):
    repo = FakeRepository()
    core = BotCore(FakeCatalog(), FakeSchedules(), repo, InMemoryDrafts(), limiter)
    return core, repo


def kb_actions(reply) -> set[Action]:
    assert reply.keyboard is not None, reply.text
    return {decode_payload(button.payload).action for button in reply.keyboard.buttons()}


async def test_new_user_sees_role_keyboard() -> None:
    core, _ = make_core()
    reply = await core.handle(1, None, None)

    assert reply.keyboard is not None
    values = {decode_payload(b.payload).value for b in reply.keyboard.buttons()}
    assert values == {"student", "teacher"}


async def test_student_flow_saves_profile() -> None:
    core, repo = make_core()

    await core.handle(1, None, None)
    reply = await core.handle(1, None, make_payload(Action.ROLE, value="student"))
    assert Action.FACULTY in kb_actions(reply)

    reply = await core.handle(1, None, make_payload(Action.FACULTY, value=36))
    assert Action.LEVEL in kb_actions(reply)

    reply = await core.handle(1, None, make_payload(Action.LEVEL, value="bachelor"))
    assert Action.COURSE in kb_actions(reply)

    reply = await core.handle(1, None, make_payload(Action.COURSE, value=1))
    assert Action.GROUP in kb_actions(reply)

    reply = await core.handle(1, None, make_payload(Action.GROUP, value=8948))
    assert kb_actions(reply) == {
        Action.TODAY,
        Action.TOMORROW,
        Action.WEEK,
        Action.CHANGE,
    }

    profile = repo.data[1]
    assert profile.role == "student"
    assert profile.group_name == "26-ПМбо-1"
    assert profile.group_id == 8948


async def test_teacher_flow() -> None:
    core, repo = make_core()

    await core.handle(2, None, None)
    reply = await core.handle(2, None, make_payload(Action.ROLE, value="teacher"))
    assert reply.keyboard is None  # ожидается ввод фамилии текстом

    reply = await core.handle(2, "'; DROP TABLE users; --", None)
    assert reply.keyboard is None and "фамилию" in reply.text.lower()

    reply = await core.handle(2, "адеева", None)
    assert Action.TEACHER in kb_actions(reply)

    await core.handle(2, None, make_payload(Action.TEACHER, value=14))
    assert repo.data[2].teacher_name == "Адеева Татьяна Николаевна"


async def test_schedule_buttons_use_profile() -> None:
    core, repo = make_core()
    await repo.save(
        UserProfile(vk_id=5, role="student", group_id=8948, group_name="26-ПМбо-1")
    )

    today = await core.handle(5, None, make_payload(Action.TODAY))
    assert today.text.startswith("DAY student 8948")
    assert Action.TODAY in kb_actions(today)

    week = await core.handle(5, None, make_payload(Action.WEEK))
    assert week.text.startswith("WEEK student 8948")


async def test_change_profile_resets() -> None:
    core, repo = make_core()
    await repo.save(
        UserProfile(vk_id=6, role="teacher", teacher_id=14, teacher_name="Тест")
    )

    reply = await core.handle(6, None, make_payload(Action.CHANGE))

    assert 6 not in repo.data
    assert Action.ROLE in kb_actions(reply)


async def test_rate_limited() -> None:
    core, _ = make_core(limiter=DenyLimiter())
    reply = await core.handle(7, None, None)
    assert "Слишком много" in reply.text


async def test_registered_user_cannot_restart_onboarding_by_button() -> None:
    core, repo = make_core()
    await repo.save(
        UserProfile(vk_id=5, role="student", group_id=8948, group_name="26-ПМбо-1")
    )

    reply = await core.handle(5, None, make_payload(Action.FACULTY, value=36))

    assert Action.TODAY in kb_actions(reply)


async def test_garbage_payload_is_safe() -> None:
    core, _ = make_core()
    reply = await core.handle(9, None, {"foo": "bar"})
    assert reply.keyboard is not None


class FailingCatalog(FakeCatalog):
    async def get_faculties(self):
        raise EiosApiError("unavailable")


async def test_first_contact_greets() -> None:
    core, _ = make_core()
    reply = await core.handle(1, None, None)
    assert "Здравствуйте" in reply.text


async def test_returning_user_gets_short_prompt() -> None:
    core, _ = make_core()
    await core.handle(1, None, None)
    await core.handle(1, None, make_payload(Action.ROLE, value="student"))

    reply = await core.handle(1, "просто текст", None)
    assert "Здравствуйте" not in reply.text


async def test_catalog_unavailable_shows_friendly_error() -> None:
    core = BotCore(
        FailingCatalog(), FakeSchedules(), FakeRepository(), InMemoryDrafts()
    )
    await core.handle(1, None, None)

    reply = await core.handle(1, None, make_payload(Action.ROLE, value="student"))
    assert "недоступен" in reply.text.lower()