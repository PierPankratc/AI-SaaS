"""Модели данных и нормализация ответов API ЭИОС КГУ.

Здесь собраны:

* перечисление :class:`EducationLevel` и классификатор ступени обучения
  по шифру группы (например, ``26-ПМбо-1`` → бакалавриат);
* «сырые» модели ответов API (с алиасами на кириллические ключи);
* доменные модели :class:`Group`, :class:`Teacher`, :class:`Lesson`,
  :class:`Schedule`, удобные для бизнес-логики;
* функции :func:`parse_groups` и :func:`parse_teachers`.

Факты по данным КГУ (проверено на ``/api/raspGrouplist`` за 2026-2027):

* реальные группы имеют код вида ``ГГ-ШИФРст-N`` (ровно два дефиса);
* служебная запись ``Нет`` не является группой (``facul``/``kurs`` = ``null``);
* шифры ступеней: ``бо/бз/бв/бс`` — бакалавриат, ``со/сз`` — специалитет,
  ``мо/мз/мв`` — магистратура, ``ао/аз`` — аспирантура, ``спо`` — колледж.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EducationLevel(str, Enum):
    """Ступень (уровень) образования."""

    BACHELOR = "bachelor"
    SPECIALIST = "specialist"
    MAGISTRACY = "magistracy"
    POSTGRADUATE = "postgraduate"
    SECONDARY = "secondary"
    UNKNOWN = "unknown"

    @property
    def title(self) -> str:
        """Человекочитаемое название ступени для сообщений ВК."""
        return _LEVEL_TITLES[self]


_LEVEL_TITLES: dict[EducationLevel, str] = {
    EducationLevel.BACHELOR: "Бакалавриат",
    EducationLevel.SPECIALIST: "Специалитет",
    EducationLevel.MAGISTRACY: "Магистратура",
    EducationLevel.POSTGRADUATE: "Аспирантура",
    EducationLevel.SECONDARY: "СПО (колледж)",
    EducationLevel.UNKNOWN: "Другое",
}

# Шифр ступени в коде группы → ступень обучения.
_SUFFIX_TO_LEVEL: dict[str, EducationLevel] = {
    "бо": EducationLevel.BACHELOR,
    "бз": EducationLevel.BACHELOR,
    "бв": EducationLevel.BACHELOR,
    "бс": EducationLevel.BACHELOR,
    "со": EducationLevel.SPECIALIST,
    "сз": EducationLevel.SPECIALIST,
    "мо": EducationLevel.MAGISTRACY,
    "мз": EducationLevel.MAGISTRACY,
    "мв": EducationLevel.MAGISTRACY,
    "ао": EducationLevel.POSTGRADUATE,
    "аз": EducationLevel.POSTGRADUATE,
    "спо": EducationLevel.SECONDARY,
}

# Порядок отображения ступеней в меню бота.
LEVEL_ORDER: tuple[EducationLevel, ...] = (
    EducationLevel.BACHELOR,
    EducationLevel.SPECIALIST,
    EducationLevel.MAGISTRACY,
    EducationLevel.POSTGRADUATE,
    EducationLevel.SECONDARY,
    EducationLevel.UNKNOWN,
)

# Короткие обозначения типа занятия в начале названия дисциплины.
_LESSON_KINDS: dict[str, str] = {
    "лек": "Лекция",
    "пр": "Практика",
    "лаб": "Лабораторная",
    "сем": "Семинар",
    "конс": "Консультация",
    "экз": "Экзамен",
    "зач": "Зачёт",
    "ср": "Самостоятельная работа",
    "пз": "Практическое занятие",
}


def split_group_name(name: str) -> tuple[str | None, str | None]:
    """Делит код группы на учебный шифр и суффикс ступени обучения.

    ``26-ПМбо-1`` → ``("ПМ", "бо")``; ``26-НПспо-1`` → ``("НП", "спо")``;
    ``Нет`` → ``(None, None)``.
    """
    parts = name.split("-")
    if len(parts) < 2:
        return None, None

    middle = parts[1]
    split_at = len(middle)
    while split_at > 0 and middle[split_at - 1].islower():
        split_at -= 1

    program = middle[:split_at] or None
    suffix = middle[split_at:] or None
    return program, suffix


def education_level_from_group_name(name: str) -> EducationLevel:
    """Определяет ступень обучения по коду группы."""
    _, suffix = split_group_name(name)
    if not suffix:
        return EducationLevel.UNKNOWN
    return _SUFFIX_TO_LEVEL.get(suffix.lower(), EducationLevel.UNKNOWN)


class RawGroup(BaseModel):
    """Группа в том виде, как её отдаёт ``/api/raspGrouplist``."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    course: int | None = Field(default=None, alias="kurs")
    faculty_id: int | None = Field(default=None, alias="facultyID")
    faculty_name: str | None = Field(default=None, alias="facul")
    year: str | None = Field(default=None, alias="yearName")


class Group(BaseModel):
    """Нормализованная учебная группа."""

    id: int
    name: str
    course: int
    faculty_id: int
    faculty_name: str
    year: str | None = None
    education_level: EducationLevel
    program_code: str | None = None

    @classmethod
    def from_raw(cls, raw: RawGroup) -> "Group":
        program, _ = split_group_name(raw.name)
        return cls(
            id=raw.id,
            name=raw.name,
            course=raw.course,
            faculty_id=raw.faculty_id,
            faculty_name=raw.faculty_name,
            year=raw.year,
            education_level=education_level_from_group_name(raw.name),
            program_code=program,
        )


class RawTeacher(BaseModel):
    """Преподаватель в том виде, как его отдаёт ``/api/raspTeacherlist``."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    department: str | None = Field(default=None, alias="kaf")
    id_from_rasp: bool = Field(default=False, alias="idFromRasp")


class Teacher(BaseModel):
    """Нормализованный преподаватель."""

    id: int
    name: str
    department: str | None = None

    @classmethod
    def from_raw(cls, raw: RawTeacher) -> "Teacher":
        return cls(id=raw.id, name=raw.name, department=raw.department)


def parse_groups(data: Any) -> list[Group]:
    """Преобразует список групп API в доменные модели.

    Служебные записи (без факультета или курса, например ``Нет``)
    отбрасываются.
    """
    if not isinstance(data, list):
        return []

    groups: list[Group] = []
    for item in data:
        raw = RawGroup.model_validate(item)
        if raw.faculty_id is None or raw.faculty_name is None or raw.course is None:
            continue
        groups.append(Group.from_raw(raw))
    return groups


def parse_teachers(data: Any) -> list[Teacher]:
    """Преобразует список преподавателей API в доменные модели.

    Дубли по ФИО сохраняются: у них разные ``id`` и может отличаться
    расписание, поэтому пользователь должен выбрать нужного.
    """
    if not isinstance(data, list):
        return []
    return [Teacher.from_raw(RawTeacher.model_validate(item)) for item in data]


class Lesson(BaseModel):
    """Одно занятие в расписании (элемент ``data.rasp``)."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(alias="код")
    lesson_date: datetime = Field(alias="дата")
    starts_at: datetime = Field(alias="датаНачала")
    ends_at: datetime = Field(alias="датаОкончания")
    start_time: str = Field(alias="начало")
    end_time: str = Field(alias="конец")
    weekday: int = Field(alias="деньНедели")
    weekday_name: str = Field(alias="день_недели")
    semester: int | None = Field(default=None, alias="код_Семестра")
    week_type: int = Field(default=0, alias="типНедели")
    subgroup: int = Field(default=0, alias="номерПодгруппы")
    subject: str = Field(alias="дисциплина")
    teacher_short: str | None = Field(default=None, alias="преподаватель")
    teacher_full: str | None = Field(default=None, alias="фиоПреподавателя")
    teacher_id: int | None = Field(default=None, alias="кодПреподавателя")
    room: str | None = Field(default=None, alias="аудитория")
    group_name: str | None = Field(default=None, alias="группа")
    group_id: int | None = Field(default=None, alias="кодГруппы")
    lesson_number: int | None = Field(default=None, alias="номерЗанятия")
    is_replacement: bool = Field(default=False, alias="замена")
    academic_year: str | None = Field(default=None, alias="учебныйГод")
    link: str | None = Field(default=None, alias="ссылка")
    color: str | None = Field(default=None, alias="цвет")

    @property
    def lesson_kind(self) -> str | None:
        """Тип занятия по префиксу дисциплины («лек» → «Лекция»)."""
        token = self.subject.split(" ", 1)[0].lower().rstrip(".")
        return _LESSON_KINDS.get(token)

    @property
    def subject_title(self) -> str:
        """Название дисциплины без префикса типа занятия."""
        head, sep, rest = self.subject.partition(" ")
        if sep and head.lower().rstrip(".") in _LESSON_KINDS and rest.strip():
            return rest
        return self.subject


class ScheduleGroupInfo(BaseModel):
    """Сведения о группе из блока ``data.info.group``."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = ""
    group_id: int | None = Field(default=None, alias="groupID")
    year: str | None = None


class ScheduleTeacherInfo(BaseModel):
    """Сведения о преподавателе из блока ``data.info.prepod``."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = ""
    user_id: int | None = Field(default=None, alias="userID")


class ScheduleInfo(BaseModel):
    """Метаданные недели расписания (блок ``data.info``)."""

    model_config = ConfigDict(populate_by_name=True)

    group: ScheduleGroupInfo | None = None
    teacher: ScheduleTeacherInfo | None = Field(default=None, alias="prepod")
    year: str | None = None
    week_number: int | None = Field(default=None, alias="curWeekNumber")
    week_start: datetime | None = Field(default=None, alias="date")
    week_end: datetime | None = Field(default=None, alias="lastDate")
    semester: int | None = Field(default=None, alias="curSem")
    uploaded_at: datetime | None = Field(default=None, alias="dateUploadingRasp")


class Schedule(BaseModel):
    """Расписание на неделю (ответ ``/api/Rasp``)."""

    model_config = ConfigDict(populate_by_name=True)

    lessons: list[Lesson] = Field(default_factory=list, alias="rasp")
    info: ScheduleInfo | None = None
    is_cyclical: bool = Field(default=False, alias="isCyclicalSchedule")

    @property
    def week_start_date(self) -> date | None:
        """Дата понедельника отображаемой недели."""
        if self.info and self.info.week_start:
            return self.info.week_start.date()
        return None
