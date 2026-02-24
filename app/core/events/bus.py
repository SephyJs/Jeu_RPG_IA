from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


EventHandler = Callable[[Any], None]


@dataclass
class EventBus:
    _subs: dict[type, list[EventHandler]] = field(default_factory=dict)

    def subscribe(self, event_type: type, handler: EventHandler) -> Callable[[], None]:
        handlers = self._subs.setdefault(event_type, [])
        handlers.append(handler)

        def _unsubscribe() -> None:
            rows = self._subs.get(event_type, [])
            self._subs[event_type] = [row for row in rows if row is not handler]

        return _unsubscribe

    def publish(self, event: Any) -> None:
        event_type = type(event)
        handlers = list(self._subs.get(event_type, []))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                continue


_GLOBAL_EVENT_BUS = EventBus()


def get_global_event_bus() -> EventBus:
    return _GLOBAL_EVENT_BUS
