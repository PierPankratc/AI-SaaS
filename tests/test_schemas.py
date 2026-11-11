"""Тесты классификации ступеней обучения и разбора справочников КГУ."""

from datetime import date, datetime

import pytest

from src.api.schemas import (
    EducationLevel,
    Schedule,
    education_level_from_group_name,
    parse_groups,
    parse_teachers,
    split_group_name,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("26-ПМбо-1", EducationLevel.BACHELOR),
        ("22-БГбз-3", EducationLevel.BACHELOR),
        ("22-ТДбв-7", EducationLevel.BACHELOR),
        ("22-Юбс-4", EducationLevel.BACHELOR),
        ("25-ЭБсз-1", EducationLevel.SPECIALIST),
        ("22-ЭБсо-1", EducationLevel.SPECIALIST),
        ("26-Ммо-1", EducationLevel.MAGISTRACY),
        ("26-СГмз-5", EducationLevel.MAGISTRACY),
        ("26-ПЭмв-1", EducationLevel.MAGISTRACY),
        ("26-МОао-1", EducationLevel.POSTGRADUATE),
        ("23-ОТаз-1", EducationLevel.POSTGRADUATE),
        ("26-НПспо-1", EducationLevel.SECONDARY),
        ("Нет", EducationLevel.UNKNOWN),
    ],
)
def test_education_level(name: str, expected: EducationLevel) -> None:
    assert education_level_from_group_name(name) is expected


def test_split_group_name() -> None:
    assert split_group_name("26-ПМбо-1") == ("ПМ", "бо")
    assert split_group_name("26-НПспо-1") == ("НП", "спо")
    assert split_group_name("22-Юбс-4") == ("Ю", "бс")
    assert split_group_name("Нет") == (None, None)


def test_parse_groups_skips_placeholder() -> None:
    data = [
        {
            "name": "26-ПМбо-1",
            "id": 8948,
            "kurs": 1,
            "facul": "ИВИТШ",
            "yearName": "2026-2027",
            "facultyID": 36,
        },
        {
            "name": "Нет",
            "id": 1,
            "kurs": None,
            "facul": None,
            "yearName": "2026-2027",
            "facultyID": None,
        },
    ]

    groups = parse_groups(data)

    assert [group.name for group in groups] == ["26-ПМбо-1"]
    group = groups[0]
    assert group.faculty_id == 36
    assert group.course == 1
    assert group.program_code == "ПМ"
    assert group.education_level is EducationLevel.BACHELOR


def test_parse_teachers_keeps_duplicate_names() -> None:
    data = [
        {
            "name": "Бойцова Светлана Владимировна",
            "kaf": None,
            "id": 51,
            "idFromRasp": False,
        },
        {
            "name": "Бойцова Светлана Владимировна",
            "kaf": None,
            "id": 52,
            "idFromRasp": False,
        },
    ]

    teachers = parse_teachers(data)

    assert {teacher.id for teacher in teachers} == {51, 52}


def test_parse_schedule_week_and_lesson() -> None:
    payload = {
        "isCyclicalSchedule": False,
        "rasp": [
            {
                "код": 6127868,
                "дата": "2026-09-01T00:00:00",
                "датаНачала": "2026-09-01T08:30:00",
                "датаОкончания": "2026-09-01T10:00:00",
                "начало": "08:30",
                "конец": "10:00",
                "деньНедели": 2,
                "день_недели": "Вторник",
                "код_Семестра": 1,
                "типНедели": 1,
                "номерПодгруппы": 0,
                "дисциплина": "пр Иностранный язык, Английский1",
                "преподаватель": "Комова С.П.",
                "аудитория": "Б-202",
                "учебныйГод": "2026-2027",
                "группа": "26-ПМбо-1",
                "замена": False,
                "кодПреподавателя": 212,
                "кодГруппы": 8948,
                "фиоПреподавателя": "Комова С. П.",
                "номерЗанятия": 1,
                "ссылка": None,
                "цвет": "#e49400",
            }
        ],
        "info": {
            "group": {"name": "26-ПМбо-1", "groupID": 8948, "year": "2026-2027"},
            "prepod": {"name": "", "userID": None},
            "year": "2026-2027",
            "curWeekNumber": 11,
            "curSem": 1,
            "date": "2026-08-31T00:00:00",
            "lastDate": "2026-09-07T00:00:00",
            "dateUploadingRasp": "2026-11-11T18:28:14",
        },
    }

    schedule = Schedule.model_validate(payload)

    assert len(schedule.lessons) == 1
    lesson = schedule.lessons[0]
    assert lesson.subject_title == "Иностранный язык, Английский1"
    assert lesson.lesson_kind == "Практика"
    assert lesson.room == "Б-202"
    assert lesson.teacher_full == "Комова С. П."
    assert lesson.subgroup == 0
    assert schedule.info is not None
    assert schedule.info.week_start == datetime(2026, 8, 31)
    assert schedule.week_start_date == date(2026, 8, 31)
