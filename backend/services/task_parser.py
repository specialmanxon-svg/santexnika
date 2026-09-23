"""Diyor Group — Voice Transcription (STT) and Task Parser Service.

Faster-Whisper ва OpenAI STT ёрдамида овозли хабарларни матнга ўгириш,
ва топшириқ матнидан:
1. Ижрочи (Assignee)
2. Топшириқ мазмуни (Task text)
3. Муддати (Deadline)
ни ажратиб олиш.
"""
import os
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

logger = logging.getLogger("task_parser")
UZ_TZ = timezone(timedelta(hours=5))

# Global Whisper cache
_whisper_model = None


def get_whisper_model():
    """Faster-Whisper моделини юклаш (CPU, int8, енгил ва тез 'tiny' ёки 'base')."""
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            # 'tiny' ёки 'base' модель
            model_size = os.getenv("WHISPER_MODEL_SIZE", "tiny")
            logger.info(f"Loading faster-whisper model: {model_size}")
            _whisper_model = WhisperModel(model_size, device="cpu", compute_type="int8")
            logger.info("faster-whisper model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load faster-whisper model: {e}")
            _whisper_model = None
    return _whisper_model


def transcribe_audio(file_path: str) -> str:
    """
    Овозли аудио файлни (ogg/mp3/webm/wav/m4a) матнга ўгириш.
    Аввал Faster-Whisper, муваффақиятсиз бўлса OpenAI Whisper STT API fallback.
    """
    if not os.path.exists(file_path):
        logger.warning(f"Audio file not found: {file_path}")
        return ""

    # 1. Faster-Whisper
    try:
        model = get_whisper_model()
        if model:
            segments, info = model.transcribe(
                file_path,
                beam_size=5,
                language="uz", # ёки auto-detect
                vad_filter=True
            )
            text_segments = [s.text.strip() for s in segments]
            full_text = " ".join(text_segments).strip()
            if full_text:
                logger.info(f"faster-whisper transcription success: {full_text[:80]}...")
                return full_text
    except Exception as e:
        logger.warning(f"faster-whisper transcription error: {e}, attempting OpenAI fallback")

    # 2. OpenAI Whisper STT Fallback
    try:
        from config import settings
        api_key = os.getenv("OPENAI_API_KEY") or getattr(settings, "openai_api_key", None)
        if api_key and not api_key.startswith("sk-test") and not api_key.startswith("sk-placeholder"):
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            with open(file_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                )
                res = transcript.text.strip()
                logger.info(f"OpenAI transcription success: {res[:80]}...")
                return res
    except Exception as ex:
        logger.error(f"OpenAI transcription fallback failed: {ex}")

    return ""


# ═══════════════ ТАҲЛИЛ ВА АЖРАТИБ ОЛИШ (NLP / REGEX) ═══════════════

MONTH_MAP = {
    "январ": 1, "январь": 1, "yanvar": 1,
    "феврал": 2, "февраль": 2, "fevral": 2,
    "март": 3, "mart": 3,
    "апрел": 4, "апрель": 4, "aprel": 4,
    "май": 5, "may": 5,
    "июн": 6, "июнь": 6, "iyun": 6,
    "июл": 7, "июль": 7, "iyul": 7,
    "август": 8, "avgust": 8,
    "сентябр": 9, "сентябрь": 9, "sentabr": 9, "sentyabr": 9,
    "октябр": 10, "октябрь": 10, "oktabr": 10, "oktyabr": 10,
    "ноябр": 11, "ноябрь": 11, "noyabr": 11,
    "декабр": 12, "декабрь": 12, "dekabr": 12,
}


def extract_deadline(text: str) -> tuple[Optional[datetime], Optional[str]]:
    """
    Матндан муддатни (deadline) аниқлаб datetime ва топилган иборани қайтариш.
    """
    now_local = datetime.now(UZ_TZ)
    text_lower = text.lower()

    # 1. Вақтни (соат:дақиқа) қидириш: 18:00, 18-00, соат 18 гача, соат 15:30
    hour = 18
    minute = 0
    time_found = False

    time_match = re.search(r"(?:соат\s*)?(\d{1,2})[:.-](\d{2})", text_lower)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        time_found = True
    else:
        hour_match = re.search(r"соат\s*(\d{1,2})(?:\s*гача|\s*да|\s*га)?", text_lower)
        if hour_match:
            hour = int(hour_match.group(1))
            minute = 0
            time_found = True

    # «кечгача» -> 18:00, «тушгача» -> 13:00, «эрталабгача» -> 09:00
    if not time_found:
        if "кечгача" in text_lower or "kechgacha" in text_lower:
            hour, minute = 18, 0
        elif "тушгача" in text_lower or "tushgacha" in text_lower:
            hour, minute = 13, 0
        elif "эрталабгача" in text_lower:
            hour, minute = 9, 0

    target_date = now_local.date()
    matched_phrase = None

    # Сана белгилари:
    # А) "эртага" / "ertaga"
    if "эртага" in text_lower or "ertaga" in text_lower:
        target_date = now_local.date() + timedelta(days=1)
        matched_phrase = "эртага"
    # Б) "индинга" / "indinga"
    elif "индинга" in text_lower or "indinga" in text_lower:
        target_date = now_local.date() + timedelta(days=2)
        matched_phrase = "индинга"
    # В) "бугун" / "bugun"
    elif "бугун" in text_lower or "bugun" in text_lower:
        target_date = now_local.date()
        matched_phrase = "бугун"
    # Г) "25-март", "25 март", "15 апрел"
    else:
        date_pattern = re.search(
            r"(\d{1,2})[-|\s]*(январ[ья]?|феврал[ья]?|март[а]?|апрел[ья]?|май|июн[ья]?|июл[ья]?|август[а]?|сентябр[ья]?|октябр[ья]?|ноябр[ья]?|декабр[ья]?|yanvar|fevral|mart|aprel|may|iyun|iyul|avgust|sentabr|sentyabr|oktabr|oktyabr|noyabr|dekabr)",
            text_lower
        )
        if date_pattern:
            d_day = int(date_pattern.group(1))
            m_str = date_pattern.group(2).rstrip("аяь")
            m_month = MONTH_MAP.get(m_str, now_local.month)
            year = now_local.year
            try:
                target_date = datetime(year, m_month, d_day).date()
                matched_phrase = date_pattern.group(0)
            except Exception:
                pass
        else:
            # "N кундан кейин"
            days_match = re.search(r"(\d+)\s*(?:кундан кейин|kun keyin)", text_lower)
            if days_match:
                days = int(days_match.group(1))
                target_date = now_local.date() + timedelta(days=days)
                matched_phrase = days_match.group(0)

    # Агар ҳеч бўлмаса вақт ёки сана топилган бўлса
    if matched_phrase or time_found:
        try:
            deadline_dt = datetime(
                target_date.year, target_date.month, target_date.day,
                hour, minute, 0, tzinfo=UZ_TZ
            )
            # Агар бугунги вақт ўтиб кетган бўлса ва "бугун" дейилмаган бўлса, эртанги кунга ўтказиш
            if deadline_dt <= now_local and not ("бугун" in text_lower or "bugun" in text_lower):
                deadline_dt += timedelta(days=1)
            return deadline_dt, (matched_phrase or f"{hour:02d}:{minute:02d}")
        except Exception:
            pass

    return None, None


CYR_TO_LAT_MAP = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'j', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'x', 'ҳ': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sh',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya', 'ў': 'o', 'ғ': 'g'
}

LAT_TO_CYR_MAP = {
    'sh': 'ш', 'ch': 'ч', 'yo': 'ё', 'yu': 'ю', 'ya': 'я', 'ts': 'ц',
    'a': 'а', 'b': 'б', 'v': 'в', 'g': 'г', 'd': 'д', 'e': 'е',
    'j': 'ж', 'z': 'з', 'i': 'и', 'y': 'й', 'k': 'к', 'l': 'л', 'm': 'м',
    'n': 'н', 'o': 'о', 'p': 'п', 'r': 'р', 's': 'с', 't': 'т', 'u': 'у',
    'f': 'ф', 'x': 'х', 'h': 'ҳ', 'q': 'қ'
}


def to_latin(text: str) -> str:
    """Кирилл матнни соддалаштирилган лотинчага ўгириш."""
    return ''.join(CYR_TO_LAT_MAP.get(ch, ch) for ch in text.lower())


def to_cyrillic(text: str) -> str:
    """Лотин матнни кириллчага ўгириш."""
    res = text.lower()
    for lat, cyr in LAT_TO_CYR_MAP.items():
        res = res.replace(lat, cyr)
    return res


def find_assignee_in_text(text: str, employees: List[Any]) -> tuple[Optional[Any], Optional[str]]:
    """
    Матндан ходимни (ижрочини) қидириб топиш.
    Кирилл ва Лотин ёзувидаги мосликларни (Латипов / Latipov, Джумаева / Jumaeva ва ҳ.к.) қўллайди.
    Қайтаради: (employee_object_or_dict, matched_name_in_text)
    """
    text_clean = text.lower().replace(":", " ").replace(",", " ").replace(".", " ")
    words = text_clean.split()
    words_lat = [to_latin(w) for w in words]
    words_cyr = [to_cyrillic(w) for w in words]

    for emp in employees:
        emp_name = getattr(emp, "employee_name", None) or (emp.get("employee_name") if isinstance(emp, dict) else None) or (emp.get("name") if isinstance(emp, dict) else None) or ""
        if not emp_name:
            continue

        emp_clean = emp_name.lower().replace(".", " ").strip()
        emp_parts = [p for p in emp_clean.split() if len(p) > 2]

        for part in emp_parts:
            part_lat = to_latin(part)
            part_cyr = to_cyrillic(part)

            for i, w in enumerate(words):
                w_lat = words_lat[i]
                w_cyr = words_cyr[i]

                # Тўғридан-тўғри ёки транслитерация бўйича текшириш
                match = (
                    (w == part) or
                    (w_lat == part_lat) or
                    (w_cyr == part_cyr) or
                    (len(w_lat) >= 4 and len(part_lat) >= 4 and (w_lat.startswith(part_lat) or part_lat.startswith(w_lat))) or
                    (len(w_cyr) >= 4 and len(part_cyr) >= 4 and (w_cyr.startswith(part_cyr) or part_cyr.startswith(w_cyr)))
                )
                if match:
                    return emp, w

    return None, None


def parse_task_instruction(text: str, employees: List[Any]) -> Dict[str, Any]:
    """
    Матн ёки транскрипция қилинган овозли хабарни тўлиқ таҳлил қилиш:
    - Ижрочи (Assignee)
    - Муддат (Deadline)
    - Топшириқ мазмуни (Task text)
    """
    raw_text = (text or "").strip()
    if not raw_text:
        return {
            "assignee": None,
            "task_text": "",
            "deadline_dt": None,
            "deadline_str": "—",
            "raw_text": ""
        }

    # 1. Ижрочини аниқлаш
    assignee, matched_assignee_word = find_assignee_in_text(raw_text, employees)

    # 2. Дедлайнни аниқлаш
    deadline_dt, deadline_phrase = extract_deadline(raw_text)

    # 3. Топшириқ мазмунини тозалаш (ижрочи исми ва муддат ибораларини олиб ташлаш)
    cleaned_task = raw_text

    # Ижрочи номи префиксини олиб ташлаш: "Латипов, ", "Латипов: ", "Латипов "
    if matched_assignee_word:
        cleaned_task = re.sub(
            rf"^(?:ҳурматли\s+)?{matched_assignee_word}[:,\s\-]+",
            "",
            cleaned_task,
            flags=re.IGNORECASE
        ).strip()

    # Муддат ибораларини топшириқ матнидан тозалаш
    deadline_patterns = [
        r"эртага\s+соат\s*\d{1,2}(?::\d{2})?\s*гача",
        r"эртага\s*\d{1,2}(?::\d{2})?\s*гача",
        r"бугун\s+соат\s*\d{1,2}(?::\d{2})?\s*гача",
        r"бугун\s*\d{1,2}(?::\d{2})?\s*гача",
        r"бугун\s+кечгача",
        r"эртага\s+кечгача",
        r"соат\s*\d{1,2}(?::\d{2})?\s*гача",
        r"\d{1,2}(?::\d{2})?\s*гача",
    ]
    for pat in deadline_patterns:
        cleaned_task = re.sub(pat, "", cleaned_task, flags=re.IGNORECASE).strip()

    cleaned_task = re.sub(r"\s+", " ", cleaned_task).strip(" ,.-:")

    deadline_str = "—"
    if deadline_dt:
        deadline_str = deadline_dt.strftime("%d.%m.%Y %H:%M")

    return {
        "assignee": assignee,
        "task_text": cleaned_task or raw_text,
        "deadline_dt": deadline_dt,
        "deadline_str": deadline_str,
        "raw_text": raw_text
    }
