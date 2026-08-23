"""External floating damage numbers for Star Empire."""

from __future__ import annotations

import math
import threading
import time
from typing import Any


_DAMAGE_TYPE_LABELS = {
    "kinetic": "Kinetic",
    "laser": "Laser",
    "thermal": "Thermal",
    "biogenic": "Biogenic",
    "mining": "Mining",
    "energy": "Energy",
}

_DAMAGE_TYPE_COLOURS = {
    "Kinetic": (205, 211, 218),
    "Laser": (82, 210, 244),
    "Thermal": (255, 144, 72),
    "Biogenic": (105, 222, 132),
    "Mining": (230, 191, 77),
    "Energy": (188, 128, 242),
    "Unknown": (126, 140, 154),
}


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
        self.feed = []
        self.feed_by_direction = {"dealt": [], "received": []}
        self.feed_scroll = {"all": 0, "dealt": 0, "received": 0}
        self.feed_row_capacity = 0
        self.feed_rect = None
        self.scroll_track_rect = None
        self.scroll_thumb_rect = None
        self.scroll_max = 0
        self.scroll_dragging = False
        self.scroll_drag_offset = 0
        self.dealt_total = 0.0
        self.received_total = 0.0
        self.dealt_hits = 0
        self.received_hits = 0
        self.window_open = True
        self.window_x = None
        self.window_y = 82
        self.window_width = 360
        self.window_height = 270
        self.min_window_width = 300
        self.min_window_height = 220
        self.window_rect = None
        self.header_rect = None
        self.close_rect = None
        self.clear_rect = None
        self.tab_rects = {}
        self.active_tab = "all"
        self.dragging = False
        self.drag_offset = (0, 0)
        self.resizing = None
        self.resize_start = None
        self.resize_margin = 7
        self.fire_intents = []
        self.fire_intent_grace = 2.0

    @staticmethod
    def _same_id(left: Any, right: Any) -> bool:
        return left is not None and right is not None and str(left) == str(right)

    @staticmethod
    def _matching_id(left: Any, right: Any) -> bool:
        return bool(
            _DamageState._id_tokens(left)
            .intersection(_DamageState._id_tokens(right))
        )

    @staticmethod
    def _id_tokens(value: Any) -> set[str]:
        if value is None:
            return set()
        text = str(value).strip().casefold()
        if not text:
            return set()
        tokens = {text}
        prefixes = (
            "npc-", "npc:", "player-", "player:", "ship-", "ship:",
            "entity-", "entity:",
        )
        changed = True
        while changed:
            changed = False
            for token in tuple(tokens):
                for prefix in prefixes:
                    if token.startswith(prefix) and len(token) > len(prefix):
                        stripped = token[len(prefix):]
                        if stripped not in tokens:
                            tokens.add(stripped)
                            changed = True
        return tokens

    @staticmethod
    def _lookup(mapping: Any, key: Any) -> dict | None:
        if not isinstance(mapping, dict):
            return None
        row = mapping.get(key)
        if row is None:
            row = mapping.get(str(key))
        if isinstance(row, dict):
            return row
        wanted = _DamageState._id_tokens(key)
        if not wanted:
            return None
        matches = [
            candidate for candidate_key, candidate in mapping.items()
            if isinstance(candidate, dict)
            and wanted.intersection(_DamageState._id_tokens(candidate_key))
        ]
        return matches[0] if len(matches) == 1 else None

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

    @staticmethod
    def _damage_type_label(hit: dict) -> str:
        value = str(hit.get("damage_type", "")).strip().casefold()
        return _DAMAGE_TYPE_LABELS.get(value, "Unknown")

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

    @staticmethod
    def _attacker_id(hit: dict) -> Any:
        fields = (
            "attacker_id", "attacker_player_id", "attacker_npc_id",
            "source_entity_id", "source_player_id", "source_npc_id",
            "shooter_id", "weapon_owner_id", "source_owner_id",
            "owner_id", "source_id", "miner_id", "player_id",
        )
        for field in fields:
            value = hit.get(field)
            if value is not None and not isinstance(value, dict):
                return value
        for container_name in ("attacker", "source", "shooter", "owner"):
            container = hit.get(container_name)
            if not isinstance(container, dict):
                continue
            for field in ("id", "player_id", "npc_id", "entity_id", "owner_id"):
                value = container.get(field)
                if value is not None:
                    return value
        return None

    @staticmethod
    def _selected_target_id(host: Any) -> Any:
        selector = getattr(host, "_selected_autofire_target_key", None)
        if callable(selector):
            try:
                selected = selector()
            except Exception:
                selected = None
            if (isinstance(selected, tuple) and len(selected) >= 2
                    and selected[1] is not None):
                return selected[1]
        for field in (
                "_targeted_npc_id", "_targeted_pid", "_targeted_asteroid_id",
                "_targeted_station_id", "_targeted_player_station_id",
                "_targeted_dungeon_id"):
            value = getattr(host, field, None)
            if value is not None:
                return value
        return None

    def _observe_local_fire(self, host: Any) -> None:
        autofire = getattr(host, "_autofire", None)
        firing = bool(getattr(host, "_space_held", False))
        firing = firing or bool(getattr(autofire, "engaged", False))
        target_id = self._selected_target_id(host)
        if not firing or target_id is None:
            return
        now = time.monotonic()
        expires = now + self.fire_intent_grace
        with self.lock:
            retained = [
                (known_target, known_expiry)
                for known_target, known_expiry in self.fire_intents
                if known_expiry >= now
                and not self._matching_id(known_target, target_id)
            ]
            retained.append((target_id, expires))
            self.fire_intents = retained[-8:]

    def _recent_local_fire_at(self, target_id: Any) -> bool:
        now = time.monotonic()
        with self.lock:
            self.fire_intents = [
                (known_target, expiry)
                for known_target, expiry in self.fire_intents
                if expiry >= now
            ]
            return any(
                self._matching_id(known_target, target_id)
                for known_target, _expiry in self.fire_intents
            )

    def _owned_by_player(self, host: Any, entity_id: Any) -> bool:
        local_id = self._local_player_id(host)
        if self._same_id(entity_id, local_id):
            return True
        row = self._entity(host, entity_id)
        if not isinstance(row, dict):
            return False
        for field in (
                "owner_id", "player_id", "drone_owner_id", "rc_owner_id",
                "source_owner_id"):
            if self._same_id(row.get(field), local_id):
                return True
        return False

    def _combat_direction(
            self, host: Any, hit: dict, target_id: Any) -> tuple[str, Any] | None:
        attacker_id = self._attacker_id(hit)
        if self._same_id(target_id, self._local_player_id(host)):
            if attacker_id is None:
                attacker_id = hit.get("entity_id")
            return "received", attacker_id
        if self._owned_by_player(host, attacker_id):
            return "dealt", attacker_id
        if self._recent_local_fire_at(target_id):
            return "dealt", self._local_player_id(host)
        return None

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

    def _target_label(
            self, host: Any, target_id: Any, kind: str = "ship") -> str:
        if kind == "ship" and self._same_id(
                target_id, self._local_player_id(host)):
            return "Player"
        row = (
            self._asteroid(host, target_id)
            if kind == "asteroid"
            else self._entity(host, target_id)
        )
        if isinstance(row, dict):
            for field in (
                    "display_name", "name", "npc_name", "ship_name",
                    "player_name", "username", "callsign", "ship_type",
                    "type"):
                value = row.get(field)
                if value is not None and str(value).strip():
                    return str(value).strip()
        prefix = "Asteroid" if kind == "asteroid" else "Target"
        return f"{prefix} {target_id}"

    def _record_window_hit(
            self, host: Any, target_id: Any, amount: float,
            direction: str, kind: str, attacker_id: Any,
            damage_type: str) -> None:
        local_hit = direction == "received"
        target_label = self._target_label(host, target_id, kind)
        attacker_label = (
            self._target_label(host, attacker_id, "ship")
            if attacker_id is not None else ""
        )
        display_label = target_label
        if local_hit and attacker_label and attacker_label != "Player":
            display_label = attacker_label
        entry = {
            "when": time.monotonic(),
            "direction": direction,
            "target": display_label,
            "attacker": attacker_label,
            "damage_type": damage_type,
            "amount": float(amount),
            "blocked": amount == 0.0,
        }
        with self.lock:
            if local_hit:
                self.received_total += amount
                self.received_hits += 1
            else:
                self.dealt_total += amount
                self.dealt_hits += 1
            for tab in ("all", direction):
                if self.feed_scroll[tab] > 0:
                    self.feed_scroll[tab] += 1
            self.feed.append(entry)
            self.feed_by_direction[direction].append(entry)

    def clear_window(self) -> None:
        with self.lock:
            self.feed.clear()
            for rows in self.feed_by_direction.values():
                rows.clear()
            for tab in self.feed_scroll:
                self.feed_scroll[tab] = 0
            self.dealt_total = 0.0
            self.received_total = 0.0
            self.dealt_hits = 0
            self.received_hits = 0

    def window_snapshot(self) -> dict:
        with self.lock:
            return {
                "dealt_total": self.dealt_total,
                "received_total": self.received_total,
                "dealt_hits": self.dealt_hits,
                "received_hits": self.received_hits,
                "feed": tuple(dict(entry) for entry in self.feed),
            }

    def filtered_feed(self, feed: Any = None) -> tuple[dict, ...]:
        if feed is None:
            with self.lock:
                if self.active_tab == "all":
                    return tuple(dict(row) for row in self.feed)
                return tuple(
                    dict(row) for row in
                    self.feed_by_direction.get(self.active_tab, ()))
        rows = tuple(feed)
        if self.active_tab == "dealt":
            rows = tuple(row for row in rows if row.get("direction") == "dealt")
        elif self.active_tab == "received":
            rows = tuple(
                row for row in rows if row.get("direction") == "received")
        return tuple(rows)

    def window_totals_snapshot(self) -> dict:
        with self.lock:
            return {
                "dealt_total": self.dealt_total,
                "received_total": self.received_total,
                "dealt_hits": self.dealt_hits,
                "received_hits": self.received_hits,
            }

    def feed_view(self, row_limit: int) -> dict:
        limit = max(0, int(row_limit))
        tab = self.active_tab if self.active_tab in self.feed_scroll else "all"
        with self.lock:
            rows = (
                self.feed if tab == "all"
                else self.feed_by_direction.get(tab, ()))
            maximum = max(0, len(rows) - limit)
            offset = max(0, min(self.feed_scroll[tab], maximum))
            self.feed_scroll[tab] = offset
            end = len(rows) - offset
            start = max(0, end - limit)
            visible = tuple(dict(row) for row in rows[start:end])
            total_rows = len(rows)
        return {
            "rows": visible,
            "total_rows": total_rows,
            "offset": offset,
            "max_scroll": maximum,
        }

    def _adjust_feed_scroll(self, delta: int, row_limit: int | None = None) -> None:
        limit = self.feed_row_capacity if row_limit is None else int(row_limit)
        tab = self.active_tab if self.active_tab in self.feed_scroll else "all"
        with self.lock:
            rows = (
                self.feed if tab == "all"
                else self.feed_by_direction.get(tab, ()))
            maximum = max(0, len(rows) - max(0, limit))
            self.feed_scroll[tab] = max(
                0, min(self.feed_scroll[tab] + int(delta), maximum))

    def _apply_scrollbar_drag(self, point: Any) -> None:
        if (self.scroll_track_rect is None or self.scroll_thumb_rect is None
                or self.scroll_max <= 0):
            return
        try:
            pointer_y = int(point[1])
            _, track_y, _, track_height = self.scroll_track_rect
            _, _, _, thumb_height = self.scroll_thumb_rect
        except (IndexError, TypeError, ValueError):
            return
        travel = max(0, track_height - thumb_height)
        if travel <= 0:
            return
        thumb_y = max(
            track_y,
            min(pointer_y - self.scroll_drag_offset, track_y + travel),
        )
        progress = (thumb_y - track_y) / travel
        offset = round(self.scroll_max * (1.0 - progress))
        with self.lock:
            self.feed_scroll[self.active_tab] = max(
                0, min(offset, self.scroll_max))

    def _queue(
        self,
        host: Any,
        target_id: Any,
        previous: float | None,
        remaining: float | None,
        position: tuple[float, float] | None,
        amount: float | None = None,
        show_zero: bool = False,
        kind: str = "ship",
        direction: str = "dealt",
        attacker_id: Any = None,
        damage_type: str = "Unknown",
    ) -> None:
        if amount is None:
            if previous is None or remaining is None or remaining >= previous:
                if not show_zero:
                    return
                amount = 0.0
            else:
                amount = previous - remaining
        if (not math.isfinite(amount) or amount < 0.0
                or (amount == 0.0 and not show_zero)):
            return
        if direction not in ("dealt", "received"):
            return
        local_hit = direction == "received"
        self._record_window_hit(
            host, target_id, amount, direction, kind, attacker_id,
            damage_type)
        self.api.logger.debug(
            "DAMAGE_EVENT_RECORDED target=%s attacker=%s amount=%.3f direction=%s",
            target_id, attacker_id, amount, direction)
        if position is None:
            return
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

    def record_ship_hit(self, host: Any, hit: Any) -> None:
        if not isinstance(hit, dict):
            return
        target_id = hit.get("target_id")
        if target_id is None:
            return
        combat = self._combat_direction(host, hit, target_id)
        if combat is None:
            self.api.logger.debug(
                "DAMAGE_EVENT_IGNORED target=%s attacker=%s fields=%s",
                target_id, self._attacker_id(hit), sorted(hit))
            return
        direction, attacker_id = combat
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
            self._world_position(host, target_id), amount=amount,
            show_zero=confirmed_zero, kind="ship", direction=direction,
            attacker_id=attacker_id,
            damage_type=self._damage_type_label(hit))

    def record_asteroid_hit(self, host: Any, hit: Any) -> None:
        if not isinstance(hit, dict):
            return
        target_id = hit.get("id")
        if target_id is None:
            return
        combat = self._combat_direction(host, hit, target_id)
        if combat is None:
            self.api.logger.debug(
                "DAMAGE_EVENT_IGNORED target=%s attacker=%s fields=%s",
                target_id, self._attacker_id(hit), sorted(hit))
            return
        direction, attacker_id = combat
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
            self._asteroid_position(host, target_id), amount=amount,
            show_zero=confirmed_zero, kind="asteroid", direction=direction,
            attacker_id=attacker_id,
            damage_type=self._damage_type_label(hit))

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
        self._observe_local_fire(host)

    def _draw_floaters(self, host: Any, surface: Any) -> None:
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

    @staticmethod
    def _inside(rect: tuple[int, int, int, int] | None, point: Any) -> bool:
        if rect is None or point is None:
            return False
        try:
            x, y = int(point[0]), int(point[1])
        except (IndexError, TypeError, ValueError):
            return False
        rx, ry, rw, rh = rect
        return rx <= x < rx + rw and ry <= y < ry + rh

    def _clamp_window(self, surface: Any) -> None:
        try:
            screen_width, screen_height = surface.get_size()
        except (AttributeError, TypeError, ValueError):
            return
        screen_width = max(1, int(screen_width))
        screen_height = max(1, int(screen_height))
        self.window_width = min(
            screen_width, max(self.min_window_width, int(self.window_width)))
        self.window_height = min(
            screen_height, max(self.min_window_height, int(self.window_height)))
        if self.window_x is None:
            self.window_x = max(0, screen_width - self.window_width - 18)
        self.window_x = max(
            0, min(int(self.window_x), max(0, screen_width - self.window_width)))
        self.window_y = max(
            0, min(int(self.window_y), max(0, screen_height - self.window_height)))

    def _resize_action(self, point: Any) -> str | None:
        if not self._inside(self.window_rect, point):
            return None
        x, y, width, height = self.window_rect
        px, py = int(point[0]), int(point[1])
        margin = self.resize_margin
        horizontal = (
            "left" if px < x + margin
            else "right" if px >= x + width - margin
            else ""
        )
        vertical = (
            "top" if py < y + margin
            else "bottom" if py >= y + height - margin
            else ""
        )
        if vertical and horizontal:
            return f"{vertical}-{horizontal}"
        return horizontal or vertical or None

    def _apply_resize(self, point: Any, screen: Any) -> None:
        if self.resizing is None or self.resize_start is None:
            return
        try:
            pointer_x, pointer_y = int(point[0]), int(point[1])
            screen_width, screen_height = screen.get_size()
        except (AttributeError, IndexError, TypeError, ValueError):
            return
        start_x, start_y, old_x, old_y, old_width, old_height = self.resize_start
        dx, dy = pointer_x - start_x, pointer_y - start_y
        left, top = old_x, old_y
        right, bottom = old_x + old_width, old_y + old_height
        min_width = min(self.min_window_width, max(1, int(screen_width)))
        min_height = min(self.min_window_height, max(1, int(screen_height)))
        if "left" in self.resizing:
            left = max(0, min(old_x + dx, right - min_width))
        if "right" in self.resizing:
            right = min(
                int(screen_width), max(left + min_width, right + dx))
        if "top" in self.resizing:
            top = max(0, min(old_y + dy, bottom - min_height))
        if "bottom" in self.resizing:
            bottom = min(
                int(screen_height), max(top + min_height, bottom + dy))
        self.window_x = left
        self.window_y = top
        self.window_width = right - left
        self.window_height = bottom - top

    @staticmethod
    def _font(host: Any, pygame: Any, size: int, bold: bool = False) -> Any:
        factory = getattr(host, "_F", None)
        if callable(factory):
            return factory(size, bold=bold)
        return pygame.font.SysFont("consolas", size, bold=bold)

    @staticmethod
    def _blit_text(
            panel: Any, font: Any, text: str, colour: tuple[int, int, int],
            x: int, y: int) -> None:
        panel.blit(font.render(str(text), True, colour), (x, y))

    def _draw_window(self, host: Any, surface: Any) -> None:
        if not self.window_open or self.pygame is None:
            self.window_rect = None
            self.header_rect = None
            self.close_rect = None
            self.clear_rect = None
            self.tab_rects = {}
            self.feed_rect = None
            self.scroll_track_rect = None
            self.scroll_thumb_rect = None
            self.scroll_max = 0
            return
        pygame = self.pygame
        self._clamp_window(surface)
        x, y = int(self.window_x), int(self.window_y)
        width, height = self.window_width, self.window_height
        self.window_rect = (x, y, width, height)
        self.header_rect = (x, y, width, 32)
        self.close_rect = (x + width - 31, y + 5, 24, 22)
        self.clear_rect = (x + width - 86, y + 6, 46, 20)
        tab_width = min(88, max(70, (width - 28) // 3))
        self.tab_rects = {
            key: (x + 10 + index * tab_width, y + 38, tab_width - 4, 24)
            for index, key in enumerate(("all", "dealt", "received"))
        }

        panel = pygame.Surface((width, height), pygame.SRCALPHA)
        panel.fill((8, 12, 18, 238))
        pygame.draw.rect(panel, (45, 58, 72, 255), (0, 0, width, height), 1)
        pygame.draw.rect(panel, (17, 25, 35, 255), (1, 1, width - 2, 31))
        pygame.draw.line(panel, (220, 70, 62), (0, 32), (width, 32), 2)

        title_font = self._font(host, pygame, 15, True)
        body_font = self._font(host, pygame, 13, False)
        value_font = self._font(host, pygame, 21, True)
        small_font = self._font(host, pygame, 11, False)
        self._blit_text(panel, title_font, "DAMAGE REPORT", (232, 238, 244), 10, 7)

        pygame.draw.rect(panel, (35, 45, 56), (width - 86, 6, 46, 20), 1)
        self._blit_text(panel, small_font, "CLEAR", (170, 184, 198), width - 80, 9)
        pygame.draw.rect(panel, (68, 36, 40), (width - 31, 5, 24, 22))
        pygame.draw.rect(panel, (116, 55, 60), (width - 31, 5, 24, 22), 1)
        self._blit_text(panel, title_font, "x", (245, 210, 212), width - 24, 6)

        for key, label in (("all", "ALL"), ("dealt", "DEALT"),
                           ("received", "RECEIVED")):
            tab_x, tab_y, tab_w, tab_h = self.tab_rects[key]
            local = (tab_x - x, tab_y - y, tab_w, tab_h)
            active = key == self.active_tab
            fill = (42, 50, 59) if active else (14, 21, 29)
            border = (220, 70, 62) if active else (42, 55, 68)
            pygame.draw.rect(panel, fill, local)
            pygame.draw.rect(panel, border, local, 1)
            self._blit_text(
                panel, small_font, label,
                (236, 239, 242) if active else (127, 141, 154),
                local[0] + 8, local[1] + 5)

        snapshot = self.window_totals_snapshot()
        card_width = (width - 30) // 2
        cards = (
            (10, "DEALT", "dealt", snapshot["dealt_total"],
             snapshot["dealt_hits"], (255, 104, 88)),
            (20 + card_width, "RECEIVED", "received",
             snapshot["received_total"], snapshot["received_hits"],
             (192, 214, 232)),
        )
        for card_x, label, key, total, hits, colour in cards:
            selected = self.active_tab in ("all", key)
            fill = (14, 21, 29) if selected else (10, 15, 21)
            border = colour if self.active_tab == key else (42, 55, 68)
            pygame.draw.rect(panel, fill, (card_x, 70, card_width, 61))
            pygame.draw.rect(panel, border, (card_x, 70, card_width, 61), 1)
            self._blit_text(panel, small_font, label, colour, card_x + 8, 76)
            self._blit_text(
                panel, value_font, f"{total:.0f}", (238, 241, 244),
                card_x + 8, 91)
            self._blit_text(
                panel, small_font, f"{hits} hits", (135, 149, 163),
                card_x + card_width - 52, 109)

        heading = {
            "all": "ALL DAMAGE HISTORY",
            "dealt": "DAMAGE DEALT HISTORY",
            "received": "DAMAGE RECEIVED HISTORY",
        }[self.active_tab]
        self._blit_text(panel, small_font, heading, (140, 154, 168), 10, 140)
        pygame.draw.line(panel, (35, 47, 59), (10, 157), (width - 10, 157), 1)
        row_start = 164
        feed_height = max(0, height - 25 - row_start)
        row_limit = max(0, feed_height // 21 + 1)
        self.feed_row_capacity = row_limit
        self.feed_rect = (x + 8, y + row_start, max(0, width - 16), feed_height)
        view = self.feed_view(row_limit)
        visible_feed = view["rows"]
        self.scroll_max = view["max_scroll"]
        self.scroll_track_rect = None
        self.scroll_thumb_rect = None
        if self.scroll_max > 0 and feed_height > 0:
            track_x = x + width - 13
            track_y = y + row_start
            track_width = 7
            thumb_height = max(
                22, round(feed_height * row_limit / view["total_rows"]))
            thumb_height = min(feed_height, thumb_height)
            travel = max(0, feed_height - thumb_height)
            progress = 1.0 - (view["offset"] / self.scroll_max)
            thumb_y = track_y + round(travel * progress)
            self.scroll_track_rect = (
                track_x, track_y, track_width, feed_height)
            self.scroll_thumb_rect = (
                track_x, thumb_y, track_width, thumb_height)
            pygame.draw.rect(
                panel, (18, 27, 36),
                (track_x - x, track_y - y, track_width, feed_height))
            pygame.draw.rect(
                panel, (75, 91, 106),
                (track_x - x, thumb_y - y, track_width, thumb_height))
            pygame.draw.rect(
                panel, (119, 137, 153),
                (track_x - x, thumb_y - y, track_width, thumb_height), 1)
        for index, entry in enumerate(reversed(visible_feed)):
            row_y = row_start + index * 21
            outgoing = entry["direction"] == "dealt"
            colour = (255, 104, 88) if outgoing else (192, 214, 232)
            marker = "OUT" if outgoing else " IN"
            amount = "BLOCK" if entry["blocked"] else f"-{entry['amount']:.0f}"
            damage_type = str(entry.get("damage_type", "Unknown"))
            type_colour = _DAMAGE_TYPE_COLOURS.get(
                damage_type, _DAMAGE_TYPE_COLOURS["Unknown"])
            target = str(entry["target"])
            target_x = 177
            target_right_margin = 22 if self.scroll_max > 0 else 10
            target_limit = max(
                8, (width - target_x - target_right_margin) // 7)
            if len(target) > target_limit:
                target = target[:max(5, target_limit - 3)] + "..."
            self._blit_text(panel, body_font, marker, colour, 10, row_y)
            self._blit_text(panel, body_font, amount, (230, 234, 238), 47, row_y)
            self._blit_text(
                panel, body_font, damage_type, type_colour, 106, row_y)
            self._blit_text(
                panel, body_font, target, (158, 170, 182), target_x, row_y)

        self._blit_text(
            panel, small_font, "F8 show/hide", (92, 106, 119), 10,
            height - 18)
        pygame.draw.line(
            panel, (82, 96, 109), (width - 13, height - 4),
            (width - 4, height - 13), 1)
        pygame.draw.line(
            panel, (82, 96, 109), (width - 8, height - 4),
            (width - 4, height - 8), 1)
        surface.blit(panel, (x, y))

    def draw(self, host: Any, surface: Any) -> None:
        if surface is None or not hasattr(surface, "get_size"):
            return
        self._draw_floaters(host, surface)
        self._draw_window(host, surface)

    def handle_event(self, host: Any, event: Any, screen: Any = None) -> bool:
        if self.host is not host or self.pygame is None:
            return False
        pygame = self.pygame
        event_type = getattr(event, "type", None)
        if (event_type == getattr(pygame, "KEYDOWN", None)
                and getattr(event, "key", None) == getattr(pygame, "K_F8", None)):
            self.window_open = not self.window_open
            self.dragging = False
            self.resizing = None
            self.resize_start = None
            self.scroll_dragging = False
            return True
        if not self.window_open:
            return False
        point = getattr(event, "pos", None)
        if point is None:
            mouse = getattr(pygame, "mouse", None)
            getter = getattr(mouse, "get_pos", None)
            point = getter() if callable(getter) else None

        if (event_type == getattr(pygame, "MOUSEMOTION", None)
                and self.scroll_dragging and point is not None):
            self._apply_scrollbar_drag(point)
            return True

        if (event_type == getattr(pygame, "MOUSEMOTION", None)
                and self.resizing is not None and point is not None):
            self._apply_resize(point, screen)
            return True

        if (event_type == getattr(pygame, "MOUSEMOTION", None)
                and self.dragging and point is not None):
            self.window_x = int(point[0]) - self.drag_offset[0]
            self.window_y = int(point[1]) - self.drag_offset[1]
            if screen is not None:
                self._clamp_window(screen)
            return True

        if event_type == getattr(pygame, "MOUSEBUTTONUP", None):
            if self.scroll_dragging:
                self.scroll_dragging = False
                return True
            if self.dragging or self.resizing is not None:
                self.dragging = False
                self.resizing = None
                self.resize_start = None
                return True
            return self._inside(self.window_rect, point)

        if event_type == getattr(pygame, "MOUSEWHEEL", None):
            if not self._inside(self.feed_rect, point):
                return False
            wheel_y = int(getattr(event, "y", 0) or 0)
            if wheel_y:
                self._adjust_feed_scroll(wheel_y * 3)
            return True

        if event_type == getattr(pygame, "MOUSEBUTTONDOWN", None):
            if not self._inside(self.window_rect, point):
                return False
            button = getattr(event, "button", None)
            if button in (4, 5) and self._inside(self.feed_rect, point):
                self._adjust_feed_scroll(3 if button == 4 else -3)
                return True
            if button != 1:
                return True
            if self._inside(self.close_rect, point):
                self.window_open = False
                self.dragging = False
                self.resizing = None
                self.resize_start = None
                self.scroll_dragging = False
                return True
            if self._inside(self.clear_rect, point):
                self.clear_window()
                return True
            for key, rect in self.tab_rects.items():
                if self._inside(rect, point):
                    self.active_tab = key
                    return True
            if self._inside(self.scroll_thumb_rect, point):
                self.scroll_dragging = True
                self.scroll_drag_offset = int(point[1]) - int(
                    self.scroll_thumb_rect[1])
                self.dragging = False
                self.resizing = None
                return True
            if self._inside(self.scroll_track_rect, point):
                if point[1] < self.scroll_thumb_rect[1]:
                    self._adjust_feed_scroll(self.feed_row_capacity)
                elif point[1] >= (
                        self.scroll_thumb_rect[1] + self.scroll_thumb_rect[3]):
                    self._adjust_feed_scroll(-self.feed_row_capacity)
                return True
            resize_action = self._resize_action(point)
            if resize_action is not None:
                self.resizing = resize_action
                self.resize_start = (
                    int(point[0]), int(point[1]), int(self.window_x),
                    int(self.window_y), self.window_width, self.window_height,
                )
                self.dragging = False
                return True
            if self._inside(self.header_rect, point):
                self.dragging = True
                self.drag_offset = (
                    int(point[0]) - int(self.window_x),
                    int(point[1]) - int(self.window_y),
                )
            return True

        mouse_events = {
            getattr(pygame, name, None)
            for name in ("MOUSEMOTION", "MOUSEWHEEL")
        }
        return event_type in mouse_events and self._inside(self.window_rect, point)

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
            self.feed.clear()
            self.fire_intents.clear()
        self.host = None
        self.pygame = None
        self.window_rect = None
        self.header_rect = None
        self.close_rect = None
        self.clear_rect = None
        self.tab_rects = {}
        self.dragging = False
        self.resizing = None
        self.resize_start = None


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

    def event(*, host: Any, event: Any, screen: Any = None, **_kwargs) -> bool:
        return state.handle_event(host, event, screen)

    api.on("client.startup", startup, priority=500)
    api.on("client.frame.begin", begin_frame, priority=500)
    api.on("client.event", event, priority=500)
    api.on("client.draw", draw, priority=500)
    api.on(
        "loader.shutdown",
        lambda *_args, **_kwargs: state.uninstall(),
        priority=500,
    )


__all__ = ("register",)
