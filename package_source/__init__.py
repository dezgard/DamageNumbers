"""External floating damage numbers for Star Empire."""

from __future__ import annotations

import math
import threading
import time
from typing import Any


class _DamageState:
    def __init__(self, api: Any) -> None:
        self.api = api
        self.host = None
        self.pygame = None
        self.lock = threading.RLock()
        self.items = []
        self.pools = {}
        self.original_hit = None
        self.original_asteroid_hit = None
        self.hit_wrapper = None
        self.asteroid_wrapper = None
        self.lifetime = 1.2
        self.rise = 46.0
        self.limit = 40

    @staticmethod
    def _same_id(left: Any, right: Any) -> bool:
        return left is not None and right is not None and str(left) == str(right)

    @staticmethod
    def _lookup(mapping: Any, key: Any) -> dict | None:
        if not isinstance(mapping, dict):
            return None
        row = mapping.get(key)
        if row is None:
            row = mapping.get(str(key))
        return row if isinstance(row, dict) else None

    def _entity(self, host: Any, target_id: Any) -> dict | None:
        try:
            with host._lock:
                row = self._lookup(getattr(host, "_remote_entities", None), target_id)
                if row is None:
                    row = self._lookup(getattr(host, "_npc_entities", None), target_id)
                return dict(row) if row is not None else None
        except (AttributeError, RuntimeError, TypeError):
            return None

    def _asteroid(self, host: Any, target_id: Any) -> dict | None:
        try:
            with host._lock:
                row = self._lookup(getattr(host, "_asteroids", None), target_id)
                return dict(row) if row is not None else None
        except (AttributeError, RuntimeError, TypeError):
            return None

    @staticmethod
    def _number(row: dict | None, field: str) -> float | None:
        try:
            value = None if row is None else row.get(field)
            number = float(value) if value is not None else None
            return number if number is not None and math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _damage(hit: dict) -> float | None:
        """Return authoritative positive damage, or None when not supplied."""
        if "damage" not in hit:
            return None
        try:
            amount = float(hit["damage"])
        except (TypeError, ValueError):
            return None
        return amount if math.isfinite(amount) and amount > 0.0 else None

    def _local_player_id(self, host: Any) -> Any:
        player_id = getattr(host, "_local_player_id", None)
        if player_id is not None:
            return player_id
        finder = getattr(host, "_find_my_entity", None)
        try:
            row = finder() if callable(finder) else None
        except Exception:
            row = None
        return row.get("player_id") if isinstance(row, dict) else None

    def _world_position(self, host: Any, target_id: Any) -> tuple[float, float] | None:
        if self._same_id(target_id, self._local_player_id(host)):
            try:
                return float(host._local_x), float(host._local_y)
            except (AttributeError, TypeError, ValueError):
                pass
        row = self._entity(host, target_id)
        if row is None:
            return None
        try:
            return (
                float(row.get("x", row.get("render_x", 0.0))),
                float(row.get("y", row.get("render_y", 0.0))),
            )
        except (TypeError, ValueError):
            return None

    def _asteroid_position(self, host: Any, target_id: Any) -> tuple[float, float] | None:
        row = self._asteroid(host, target_id)
        if row is None:
            return None
        resolver = getattr(host, "_asteroid_world_pos", None)
        try:
            if callable(resolver):
                x, y = resolver(row)
            else:
                x, y = row.get("x"), row.get("y")
            return float(x), float(y)
        except (TypeError, ValueError):
            return None

    def _queue(
        self,
        host: Any,
        target_id: Any,
        previous: float | None,
        remaining: float | None,
        position: tuple[float, float] | None,
        amount: float | None = None,
        show_zero: bool = False,
    ) -> None:
        if amount is None:
            if previous is None or remaining is None or remaining >= previous:
                if not show_zero:
                    return
                amount = 0.0
            else:
                amount = previous - remaining
        if (not math.isfinite(amount) or amount < 0.0
                or (amount == 0.0 and not show_zero)
                or position is None):
            return
        local_hit = self._same_id(target_id, self._local_player_id(host))
        colour = (225, 230, 235) if local_hit else (255, 90, 80)
        item = (
            position[0], position[1],
            ("0" if amount == 0.0 else f"-{amount:.0f}"), colour,
            time.monotonic(), not local_hit,
        )
        with self.lock:
            self.items.append(item)
            if len(self.items) > self.limit:
                del self.items[:-self.limit]
        self.api.logger.debug(
            "DAMAGE_NUMBER_QUEUED target=%s amount=%.3f",
            target_id, amount)

    def record_ship_hit(self, host: Any, hit: Any) -> None:
        if not isinstance(hit, dict):
            return
        target_id = hit.get("target_id")
        if target_id is None:
            return
        amount = self._damage(hit)
        raw_remaining = hit.get("shields_remaining", hit.get("shields"))
        try:
            remaining = (
                float(raw_remaining) if raw_remaining is not None else None)
        except (TypeError, ValueError):
            remaining = None
        if remaining is not None and not math.isfinite(remaining):
            remaining = None
        if remaining is not None and remaining <= 0.0:
            remaining = 0.0
        confirmed_zero = remaining == 0.0
        if "damage" in hit and amount is None and not confirmed_zero:
            return
        if amount is None and remaining is None:
            return
        key = "ship:" + str(target_id)
        current = self._number(self._entity(host, target_id), "shields")
        with self.lock:
            stored = self.pools.get(key)
            if remaining is not None:
                self.pools[key] = remaining
        previous = (
            current
            if (remaining is not None and current is not None
                and current != remaining)
            else stored
        )
        self._queue(
            host, target_id, previous, remaining,
            self._world_position(host, target_id),
            amount,
            confirmed_zero,
        )

    def record_asteroid_hit(self, host: Any, hit: Any) -> None:
        if not isinstance(hit, dict):
            return
        target_id = hit.get("id")
        if target_id is None:
            return
        amount = self._damage(hit)
        try:
            raw_remaining = hit.get("health_remaining")
            remaining = (
                float(raw_remaining) if raw_remaining is not None else None)
        except (TypeError, ValueError):
            remaining = None
        if remaining is not None and not math.isfinite(remaining):
            remaining = None
        if remaining is not None and remaining <= 0.0:
            remaining = 0.0
        confirmed_zero = remaining == 0.0
        if "damage" in hit and amount is None and not confirmed_zero:
            return
        if amount is None and remaining is None:
            return
        key = "asteroid:" + str(target_id)
        current = self._number(self._asteroid(host, target_id), "health")
        with self.lock:
            stored = self.pools.get(key)
            if remaining is not None:
                self.pools[key] = remaining
        previous = (
            current
            if (remaining is not None and current is not None
                and current != remaining)
            else stored
        )
        self._queue(
            host, target_id, previous, remaining,
            self._asteroid_position(host, target_id),
            amount,
            confirmed_zero,
        )

    def snapshot(self, host: Any) -> None:
        snapshot = {}
        try:
            with host._lock:
                entities = list(getattr(host, "_remote_entities", {}).items())
                entities += list(getattr(host, "_npc_entities", {}).items())
                asteroids = list(getattr(host, "_asteroids", {}).items())
        except (AttributeError, RuntimeError, TypeError):
            return
        for target_id, row in entities:
            value = self._number(row, "shields")
            if value is not None:
                snapshot["ship:" + str(target_id)] = value
        for target_id, row in asteroids:
            value = self._number(row, "health")
            if value is not None:
                snapshot["asteroid:" + str(target_id)] = value
        with self.lock:
            for key, value in snapshot.items():
                previous = self.pools.get(key)
                # A lower snapshot may be the just-applied hit racing ahead of
                # register_hit().  Keep the older pool so the wrapper can
                # calculate that damage.  A higher value is regeneration and
                # becomes the next valid baseline.
                if previous is None or value > previous:
                    self.pools[key] = value

    def draw(self, host: Any, surface: Any) -> None:
        if surface is None or not hasattr(surface, "get_size"):
            return
        now = time.monotonic()
        with self.lock:
            snapshot = list(self.items)
        if not snapshot:
            return
        width, height = surface.get_size()
        font_factory = getattr(host, "_F", None)
        if callable(font_factory):
            small_font = font_factory(13, bold=True)
            large_font = font_factory(26, bold=True)
        else:
            small_font = self.pygame.font.SysFont("consolas", 13, bold=True)
            large_font = self.pygame.font.SysFont("consolas", 26, bold=True)
        lifetime = max(0.1, float(self.lifetime))
        alive = []
        for world_x, world_y, text, colour, born, large in snapshot:
            age = now - born
            if age >= lifetime:
                continue
            alive.append((world_x, world_y, text, colour, born, large))
            progress = age / lifetime
            screen_x = (world_x - host._cam_x) * host._zoom + width * 0.5
            screen_y = (
                (world_y - host._cam_y) * host._zoom + height * 0.5
                - progress * self.rise
            )
            if not (-80 <= screen_x <= width + 80 and -80 <= screen_y <= height + 80):
                continue
            glyphs = (large_font if large else small_font).render(text, True, colour)
            glyphs.set_alpha(max(0, min(255, round(255 * (1.0 - progress)))))
            surface.blit(
                glyphs,
                (round(screen_x) - glyphs.get_width() // 2, round(screen_y)),
            )
        with self.lock:
            self.items = alive

    def install(self, host: Any, pygame: Any) -> None:
        if self.host is not None:
            if self.host is host:
                return
            raise RuntimeError("Damage Numbers is already attached to another client")
        host_type = type(host)
        original_hit = getattr(host_type, "register_hit", None)
        original_asteroid = getattr(host_type, "register_asteroid_hit", None)
        if not callable(original_hit) or not callable(original_asteroid):
            raise RuntimeError("compatible combat callbacks are unavailable")
        state = self

        def hit_wrapper(instance, hit):
            if instance is state.host:
                try:
                    state.record_ship_hit(instance, hit)
                except Exception:
                    state.api.logger.exception("damage-number ship hit failed")
            return original_hit(instance, hit)

        def asteroid_wrapper(instance, hit):
            if instance is state.host:
                try:
                    state.record_asteroid_hit(instance, hit)
                except Exception:
                    state.api.logger.exception("damage-number asteroid hit failed")
            return original_asteroid(instance, hit)

        self.host = host
        self.pygame = pygame
        self.original_hit = original_hit
        self.original_asteroid_hit = original_asteroid
        self.hit_wrapper = hit_wrapper
        self.asteroid_wrapper = asteroid_wrapper
        host_type.register_hit = hit_wrapper
        host_type.register_asteroid_hit = asteroid_wrapper
        self.snapshot(host)

    def uninstall(self) -> None:
        host = self.host
        if host is not None:
            host_type = type(host)
            if getattr(host_type, "register_hit", None) is self.hit_wrapper:
                host_type.register_hit = self.original_hit
            if getattr(host_type, "register_asteroid_hit", None) is self.asteroid_wrapper:
                host_type.register_asteroid_hit = self.original_asteroid_hit
        with self.lock:
            self.items.clear()
            self.pools.clear()
        self.host = None
        self.pygame = None


def register(api: Any) -> None:
    """Register Damage Numbers with Star Empire Mod Loader API 1."""
    if getattr(api, "loader_api_version", 0) < 1:
        raise RuntimeError("Damage Numbers requires loader API 1")
    state = _DamageState(api)

    def startup(*, host: Any, pygame: Any, **_kwargs) -> bool:
        state.install(host, pygame)
        api.logger.info("DAMAGE_NUMBERS_STARTED version=%s", api.version)
        return True

    def begin_frame(*, host: Any, **_kwargs) -> None:
        if state.host is host:
            state.snapshot(host)

    def draw(*, host: Any, screen: Any, **_kwargs) -> None:
        if state.host is host:
            state.draw(host, screen)

    api.on("client.startup", startup, priority=500)
    api.on("client.frame.begin", begin_frame, priority=500)
    api.on("client.draw", draw, priority=500)
    api.on(
        "loader.shutdown",
        lambda *_args, **_kwargs: state.uninstall(),
        priority=500,
    )


__all__ = ("register",)
