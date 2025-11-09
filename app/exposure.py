from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from .store import SlotInstance


@dataclass
class ExposureCacheEntry:
    slot_ids: List[str]
    expires_at: datetime


class ExposureCache:
    def __init__(self) -> None:
        self._cache: Dict[str, ExposureCacheEntry] = {}

    def get(self, key: str, now: datetime) -> Optional[List[str]]:
        entry = self._cache.get(key)
        if not entry:
            return None
        if entry.expires_at <= now:
            self._cache.pop(key, None)
            return None
        return entry.slot_ids

    def set(self, key: str, slot_ids: List[str], ttl_seconds: int, now: datetime) -> None:
        self._cache[key] = ExposureCacheEntry(
            slot_ids=list(slot_ids), expires_at=now + timedelta(seconds=ttl_seconds)
        )


cache = ExposureCache()


def _day_part(hour: int) -> str:
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "other"


def _seed_value(*parts: str) -> int:
    joined = "|".join(parts)
    digest = hashlib.sha256(joined.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _deterministic_shuffle(slots: List[SlotInstance], seed: int) -> List[SlotInstance]:
    rng = random.Random(seed)
    cloned = list(slots)
    rng.shuffle(cloned)
    return cloned


def _rand_choice(seed: int) -> float:
    rng = random.Random(seed)
    return rng.random()


def _clamp_exposure_count(total_available: int, seed: int) -> int:
    if total_available <= 5:
        return total_available
    base = 3
    first_roll = _rand_choice(seed)
    if first_roll < 0.15:
        return 4
    if first_roll < 0.30:
        return 5
    return base


def _group_by_day_part(slots: Iterable[SlotInstance], tz: ZoneInfo) -> Dict[str, List[SlotInstance]]:
    buckets: Dict[str, List[SlotInstance]] = {"morning": [], "afternoon": [], "evening": [], "other": []}
    for slot in slots:
        local_start = slot.start_at.astimezone(tz)
        buckets[_day_part(local_start.hour)].append(slot)
    return buckets


def select_exposed_slots(
    slots: List[SlotInstance],
    *,
    location_timezone: str,
    user_key: str,
    date_key: str,
    person_key: str,
    cache_ttl_seconds: int = 420,
) -> List[SlotInstance]:
    now = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
    cache_key = f"expose:{user_key}:{date_key}:{person_key or 'all'}"
    cached = cache.get(cache_key, now)
    if cached:
        slot_map = {slot.id: slot for slot in slots}
        preserved = [slot_map[slot_id] for slot_id in cached if slot_id in slot_map]
        if preserved:
            return preserved

    tz = ZoneInfo(location_timezone)
    seed = _seed_value(user_key, date_key, person_key or "all", str(now.hour))
    shuffled = _deterministic_shuffle(slots, seed)
    total = len(shuffled)
    k = min(total, max(2, min(5, _clamp_exposure_count(total, seed))))

    buckets = _group_by_day_part(shuffled, tz)
    pick: List[SlotInstance] = []
    for part in ("morning", "afternoon", "evening"):
        if buckets[part] and len(pick) < k:
            pick.append(buckets[part].pop(0))
    if len(pick) < k:
        remainder = [slot for slot in shuffled if slot not in pick]
        pick.extend(remainder[: k - len(pick)])

    cache.set(cache_key, [slot.id for slot in pick], cache_ttl_seconds, now)
    return pick
