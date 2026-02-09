from __future__ import annotations

MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24
MINUTES_PER_DAY = HOURS_PER_DAY * MINUTES_PER_HOUR

DAYS_PER_MONTH = 30
MONTHS = [
    "Aubefer",
    "Brumelune",
    "Cendreflor",
    "Solebois",
    "Ventclair",
    "Braisecime",
    "Nuitselve",
    "Sangrune",
    "Pluiegris",
    "Saphrenuit",
    "Roncefroid",
    "Etoilegivre",
]
WEEKDAYS = [
    "Lunegarde",
    "Marteflamme",
    "Brisefeuille",
    "Sombretour",
    "Forgejour",
    "Veillebrume",
    "Reposdesombres",
]
START_YEAR = 472


def minute_of_day(world_time_minutes: int) -> int:
    total = max(0, int(world_time_minutes))
    return total % MINUTES_PER_DAY


def day_index(world_time_minutes: int) -> int:
    total = max(0, int(world_time_minutes))
    return total // MINUTES_PER_DAY


def hour_minute(world_time_minutes: int) -> tuple[int, int]:
    mod = minute_of_day(world_time_minutes)
    return mod // MINUTES_PER_HOUR, mod % MINUTES_PER_HOUR


def time_period_label(world_time_minutes: int) -> str:
    hour, _ = hour_minute(world_time_minutes)
    if hour < 5:
        return "Nuit profonde"
    if hour < 8:
        return "Aube"
    if hour < 18:
        return "Jour"
    if hour < 22:
        return "Crepuscule"
    return "Nuit"


def format_hour_label(hour: int, minute: int = 0) -> str:
    hh = int(hour) % 24
    mm = max(0, min(59, int(minute)))
    return f"{hh:02d}h{mm:02d}"


def _calendar_parts(world_time_minutes: int) -> tuple[int, int, int, int]:
    total_days = day_index(world_time_minutes)
    days_per_year = DAYS_PER_MONTH * len(MONTHS)

    year = START_YEAR + (total_days // days_per_year)
    day_in_year = total_days % days_per_year
    month_index = day_in_year // DAYS_PER_MONTH
    day_in_month = (day_in_year % DAYS_PER_MONTH) + 1
    weekday_index = total_days % len(WEEKDAYS)
    return year, month_index, day_in_month, weekday_index


def format_fantasy_datetime(world_time_minutes: int) -> str:
    year, month_index, day_in_month, weekday_index = _calendar_parts(world_time_minutes)
    hour, minute = hour_minute(world_time_minutes)
    month = MONTHS[month_index]
    weekday = WEEKDAYS[weekday_index]
    period = time_period_label(world_time_minutes)
    return f"{weekday}, {day_in_month:02d} {month}, An {year} - {hour:02d}:{minute:02d} ({period})"


def format_fantasy_time(world_time_minutes: int) -> str:
    hour, minute = hour_minute(world_time_minutes)
    return f"{hour:02d}:{minute:02d} ({time_period_label(world_time_minutes)})"

