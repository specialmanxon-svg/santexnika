"""Diyor Group — Voice Transcription (STT) and Task Parser Service.

Faster-Whisper ва OpenAI STT ёрдамида овозли хабарларни матнга ўгириш (Ўзбек ва Рус тиллари),
ҳамда топшириқ матнидан:
1. Ижрочи (Assignee) — исм, фамилия ва тахаллуслар (aliases) луғати орқали
2. Муддати (Deadline) — ўзбек ва рус тилларидаги муддат иборалари
3. Топшириқ мазмуни (Task text) — тозаланган ва муддат сўзларидан ҳоли матн
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

# Ходимларнинг исм ва фамилияларига мос тахаллуслар / қидирув луғати (Aliases)
# Каххоров Ф. К. -> "Каххоров", "Фаррух", "Farrux", "Qahhorov"
EMPLOYEE_ALIASES: Dict[str, List[str]] = {
    "latipov": ["латипов", "latipov", "феруз", "feruz", "feruzbek", "ферузбек"],
    "рахманова": ["рахманова", "рахмонова", "raxmanova", "raxmonova", "rahmanova", "севара", "sevara"],
    "каххоров": ["каххоров", "каххаров", "қаҳҳоров", "қаххоров", "qahhorov", "kaxxorov", "kakharov", "farrux", "фаррух", "фаррухбек", "farrukh"],
    "джумаева": ["джумаева", "жумаева", "jumaeva", "djumaeva", "jumayeva", "sayyora", "сайёра", "саёра", "sayora"],
    "зоиров": ["зоиров", "zoirov", "алишер", "alisher", "акмал", "akmal", "азиз", "aziz"],
    "фармонова": ["фармонова", "фарманова", "farmonova", "farmanova", "нодира", "nodira"],
}


def get_whisper_model():
    """Faster-Whisper моделини юклаш (CPU, int8, енгил ва тез 'tiny' ёки 'base')."""
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
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
    Ўзбек (лотин/кирилл) ва Рус тилларини автоматик аниқлайди (auto-detect).
    Аввал Faster-Whisper, муваффақиятсиз бўлса OpenAI Whisper STT API fallback.
    """
    if not os.path.exists(file_path):
        logger.warning(f"Audio file not found: {file_path}")
        return ""

    # 1. Faster-Whisper (language=None auto-detects between Uzbek and Russian)
    try:
        model = get_whisper_model()
        if model:
            segments, info = model.transcribe(
                file_path,
                beam_size=5,
                language=None,  # Автоматик тил аниқлаш (uz, ru)
                vad_filter=True
            )
            text_segments = [s.text.strip() for s in segments]
            full_text = " ".join(text_segments).strip()
            if full_text:
                logger.info(f"faster-whisper transcription success: {full_text[:80]}... (lang: {info.language})")
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


# ═══════════════ ТРАНСЛИТЕРАЦИЯ ВА ЛИНГВИСТИК ТАҲЛИЛ ═══════════════

CYR_TO_LAT_MAP = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'j', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'x', 'ҳ': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sh',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya', 'ў': 'o', 'ғ': 'g', 'қ': 'q'
}

LAT_TO_CYR_MAP = {
    'sh': 'ш', 'ch': 'ч', 'yo': 'ё', 'yu': 'ю', 'ya': 'я', 'ts': 'ц',
    'a': 'а', 'b': 'б', 'v': 'в', 'g': 'г', 'd': 'д', 'e': 'е',
    'j': 'ж', 'z': 'з', 'i': 'и', 'y': 'й', 'k': 'к', 'l': 'л', 'м': 'm',
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
    Матндан ходимни (ижрочини) исм, фамилия ва тахаллуслар (aliases) орқали қидириб топиш.
    Кирилл ва Лотин ёзувидаги барча вариантларни (Каххоров / Farrux / Qahhorov ва ҳ.к.) қўллайди.
    Қайтаради: (employee_object_or_dict, matched_name_in_text)
    """
    text_clean = text.lower().replace(":", " ").replace(",", " ").replace(".", " ").replace("!", " ")
    words = text_clean.split()
    words_lat = [to_latin(w) for w in words]
    words_cyr = [to_cyrillic(w) for w in words]

    for emp in employees:
        emp_name = getattr(emp, "employee_name", None) or (emp.get("employee_name") if isinstance(emp, dict) else None) or (emp.get("name") if isinstance(emp, dict) else None) or ""
        if not emp_name:
            continue

        emp_clean = emp_name.lower().replace(".", " ").strip()
        parts = [p for p in emp_clean.split() if len(p) > 2]

        all_aliases = []
        for p in parts:
            p_lat = to_latin(p)
            for k, alias_list in EMPLOYEE_ALIASES.items():
                if k in p.lower() or k in p_lat:
                    all_aliases.extend(alias_list)

        for p in parts:
            all_aliases.append(p)
            all_aliases.append(to_latin(p))
            all_aliases.append(to_cyrillic(p))

        for i, w in enumerate(words):
            w_l = words_lat[i]
            w_c = words_cyr[i]
            for alias in all_aliases:
                al_l = to_latin(alias)
                al_c = to_cyrillic(alias)
                if w == alias or w_l == al_l or w_c == al_c:
                    return emp, w
                if len(w_l) >= 4 and len(al_l) >= 4 and (w_l.startswith(al_l) or al_l.startswith(w_l)):
                    return emp, w
                if len(w_c) >= 4 and len(al_c) >= 4 and (w_c.startswith(al_c) or al_c.startswith(w_c)):
                    return emp, w

    return None, None


# ═══════════════ МУДДАТНИ АНИҚЛАШ (ЎЗБЕК + РУС ТИЛЛАРИ) ═══════════════

MONTH_MAP = {
    "январ": 1, "январь": 1, "января": 1, "yanvar": 1,
    "феврал": 2, "февраль": 2, "февраля": 2, "fevral": 2,
    "март": 3, "марта": 3, "mart": 3,
    "апрел": 4, "апрель": 4, "апреля": 4, "aprel": 4,
    "май": 5, "мая": 5, "may": 5,
    "июн": 6, "июнь": 6, "июня": 6, "iyun": 6,
    "июл": 7, "июль": 7, "июля": 7, "iyul": 7,
    "август": 8, "августа": 8, "avgust": 8,
    "сентябр": 9, "сентябрь": 9, "сентября": 9, "sentabr": 9, "sentyabr": 9,
    "октябр": 10, "октябрь": 10, "октября": 10, "oktabr": 10, "oktyabr": 10,
    "ноябр": 11, "ноябрь": 11, "ноября": 11, "noyabr": 11,
    "декабр": 12, "декабрь": 12, "декабря": 12, "dekabr": 12,
}


def extract_deadline(text: str) -> tuple[Optional[datetime], Optional[str]]:
    """
    Матндан муддатни (deadline) аниқлаб datetime ва топилган иборани қайтариш.
    Ўзбек (соат 18 гача, эртага, бугун, тушгача) ва рус (до 18:00, завтра, сегодня, до обеда)
    ибораларини бирдек таҳлил қилади.
    """
    now_local = datetime.now(UZ_TZ)
    text_lower = text.lower()

    # 1. Нисбий соатлар: "через 2 часа", "3 соатдан кейин", "2 soatdan keyin"
    rel_hours_match = (
        re.search(r"(?:через|кейин)\s*(\d+)\s*(?:час[а-я]*|соат[а-я]*|soat[a-z]*)", text_lower) or
        re.search(r"(\d+)\s*(?:соатдан кейин|soatdan keyin)", text_lower)
    )
    if rel_hours_match:
        hrs = int(rel_hours_match.group(1))
        dl = now_local + timedelta(hours=hrs)
        return dl, f"{hrs} соатдан кейин"

    # 2. Аниқ соат: 18:00, 18-00, до 18:00, соат 18 гача, к 17:00, в 14:30
    hour = 18
    minute = 0
    time_found = False

    time_match = re.search(r"(?:соат|soat|до|к|в)?\s*(\d{1,2})[:.-](\d{2})", text_lower)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        time_found = True
    else:
        hour_match = re.search(r"(?:соат|soat|до|к|в)\s*(\d{1,2})(?:\s*гача|\s*га|\s*да|\s*часам|\s*часов)?", text_lower)
        if hour_match:
            hour = int(hour_match.group(1))
            minute = 0
            time_found = True

    # «кечгача» / «до вечера» / «кун охиригача» -> 18:00, «тушгача» / «до обеда» -> 13:00, «эрталабгача» / «до утра» -> 09:00
    if not time_found:
        if any(w in text_lower for w in ["кечгача", "kechgacha", "кун охиригача", "до вечера", "до конца дня"]):
            hour, minute = 18, 0
            time_found = True
        elif any(w in text_lower for w in ["тушгача", "tushgacha", "до обеда"]):
            hour, minute = 13, 0
            time_found = True
        elif any(w in text_lower for w in ["эрталабгача", "ertalabgacha", "до утра"]):
            hour, minute = 9, 0
            time_found = True

    target_date = now_local.date()
    matched_phrase = None

    # Сана белгилари (Ўзбек ва Рус тилларида):
    if any(w in text_lower for w in ["эртага", "ertaga", "завтра"]):
        target_date = now_local.date() + timedelta(days=1)
        matched_phrase = "эртага"
    elif any(w in text_lower for w in ["индинга", "indinga", "послезавтра"]):
        target_date = now_local.date() + timedelta(days=2)
        matched_phrase = "индинга"
    elif any(w in text_lower for w in ["бугун", "bugun", "сегодня"]):
        target_date = now_local.date()
        matched_phrase = "бугун"
    else:
        # "25-март", "15 апреля", "10 may"
        date_pattern = re.search(
            r"(\d{1,2})[-|\s]*(январ[ья]?|феврал[ья]?|март[а]?|апрел[ья]?|май|мая|июн[ья]?|июл[ья]?|август[а]?|сентябр[ья]?|октябр[ья]?|ноябр[ья]?|декабр[ья]?|yanvar|fevral|mart|aprel|may|iyun|iyul|avgust|sentabr|sentyabr|oktabr|oktyabr|noyabr|dekabr)",
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
            rel_days_match = re.search(r"(\d+)\s*(?:кундан кейин|kun keyin|дня|дней)", text_lower)
            if rel_days_match:
                days = int(rel_days_match.group(1))
                target_date = now_local.date() + timedelta(days=days)
                matched_phrase = rel_days_match.group(0)

    if matched_phrase or time_found:
        try:
            deadline_dt = datetime(
                target_date.year, target_date.month, target_date.day,
                hour, minute, 0, tzinfo=UZ_TZ
            )
            # Агар бугунги вақт ўтиб кетган бўлса ва "бугун / сегодня" дейилмаган бўлса, эртанги кунга ўтказиш
            if deadline_dt <= now_local and not any(w in text_lower for w in ["бугун", "bugun", "сегодня"]):
                deadline_dt += timedelta(days=1)
            return deadline_dt, (matched_phrase or f"{hour:02d}:{minute:02d}")
        except Exception:
            pass

    return None, None


def clean_task_text(raw_text: str, matched_assignee_word: Optional[str]) -> str:
    """
    Топшириқ мазмунини ижрочи исми, мурожаатлар ҳамда муддат сўзларидан тозалаш.
    «Муддат», «срок», «дедлайн», «эртага соат 18:00 гача» сўзлари матнда қолиб кетмайди.
    """
    cleaned = raw_text

    # 1. Ижрочи исми префикси ва суффиксини олиб ташлаш
    if matched_assignee_word:
        cleaned = re.sub(
            rf"^(?:ҳурматли\s+|уважаемый\s+)?{re.escape(matched_assignee_word)}[:,\s\-]+",
            "",
            cleaned,
            flags=re.IGNORECASE
        ).strip()
        cleaned = re.sub(
            rf"[,;\s\-]+{re.escape(matched_assignee_word)}$",
            "",
            cleaned,
            flags=re.IGNORECASE
        ).strip()

    # 2. Муддат ибораларини тозалаш (Ўзбек ва Рус тиллари)
    deadline_clauses = [
        # Муддат: ... or Срок: ... to end or comma
        r"[,;\s]*(?:муддат[и]?|срок|дедлайн)[:\s]+[^,\n]+",
        # "ertaga soat 17:00 gacha", "эртага соат 18:00 гача", "завтра до 18:00"
        r"[,;\s]*(?:эртага|ertaga|бугун|bugun|завтра|сегодня|индинга|послезавтра)\s+(?:соат|soat|до|к|в)?\s*\d{1,2}(?:[:.-]\d{2})?\s*(?:гача|gacha|га|ga|да|da|часам|часов)?",
        # "эртага кечгача", "ertaga kechgacha", "бугун тушгача", "bugun tushgacha"
        r"[,;\s]*(?:эртага|ertaga|бугун|bugun|завтра|сегодня)\s+(?:кечгача|kechgacha|тушгача|tushgacha|эрталабгача|ertalabgacha|до вечера|до обеда|до утра)",
        # Standalone "кечгача", "тушгача", "до вечера", etc.
        r"[,;\s]*(?:кечгача|kechgacha|кун охиригача|до вечера|до конца дня)",
        r"[,;\s]*(?:тушгача|tushgacha|до обеда)",
        r"[,;\s]*(?:эрталабгача|ertalabgacha|до утра)",
        # "соат 18:00 гача", "soat 18:00 gacha", "до 18:00"
        r"[,;\s]*(?:соат|soat|до|к|в)\s*\d{1,2}(?:[:.-]\d{2})?\s*(?:гача|gacha|га|ga|да|da|часам|часов)?",
        r"[,;\s]*\d{1,2}(?:[:.-]\d{2})\s*(?:гача|gacha|га|ga|да|da)?",
        # Standalone "эртагача", "ertagacha"
        r"[,;\s]*(?:эртагача|ertagacha)",
        # Lingering words "муддат", "срок", "дедлайн"
        r"[,;\s]*(?:муддат[и]?|срок|дедлайн)\b",
    ]

    for pat in deadline_clauses:
        cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE).strip()

    # Ортиқча бўшлиқ ва тиниш белгиларини тозалаш
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-:;")
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]

    return cleaned or raw_text


def parse_task_instruction(text: str, employees: List[Any]) -> Dict[str, Any]:
    """
    Матн ёки транскрипция қилинган овозли хабарни тўлиқ таҳлил қилиш:
    - Ижрочи (Assignee)
    - Муддат (Deadline)
    - Топшириқ мазмуни (Task text) — тозаланган
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

    # 3. Топшириқ мазмунини тозалаш
    cleaned_task = clean_task_text(raw_text, matched_assignee_word)

    deadline_str = "—"
    if deadline_dt:
        deadline_str = deadline_dt.strftime("%d.%m.%Y %H:%M")

    return {
        "assignee": assignee,
        "task_text": cleaned_task,
        "deadline_dt": deadline_dt,
        "deadline_str": deadline_str,
        "raw_text": raw_text
    }
