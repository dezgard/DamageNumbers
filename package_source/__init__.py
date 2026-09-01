"""External floating damage numbers for Star Empire."""

from __future__ import annotations

import json
import math
import threading
import time
import types
from collections import deque
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

_PROTOCOL_DIAGNOSTIC_TOKENS = frozenset((
    "DUNGEON_FRAME",
    "DUNGEON_AI_STATE",
    "DUNGEON_AI_JOINED",
    "DUNGEON_AI_PROJECTILES",
    "DUNGEON_AI_FIRE",
    "DUNGEON_AI_BEAM",
    "DUNGEON_AI_HIT",
    "DUNGEON_AI_HIT_ENTITY",
))
_PROTOCOL_DIAGNOSTIC_KEYWORDS = (
    "DOT", "BURN", "EFFECT", "STATUS",
)


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
        self.original_event_methods = {}
        self.event_wrappers = {}
        self.protocol_context = threading.local()
        self.protocol_handler_entries = {}
        self.protocol_route_entry = None
        self.protocol_packet_shapes = set()
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
        self.encounter_stats = {
            "dealt": self._empty_direction_stats(),
            "received": self._empty_direction_stats(),
        }
        self.encounter_started_at = None
        self.encounter_last_hit_at = None
        self.encounter_energy_used = 0.0
        self.energy_previous = None
        self.energy_previous_at = None
        self.energy_previous_max = None
        self.energy_observation_token = None
        self.energy_recent_spend = deque()
        self.energy_prehit_seconds = 2.0
        self.energy_sample_max_gap = 2.0
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
        self.weapon_tracks = {}
        self.projectile_payload_metadata = {}
        self.consumed_weapon_tracks = {}
        # Projectile IDs are only trusted at the impact moment.  A larger
        # window lets a prior slow round name a later rapid-fire hit.
        # Retain the last correction through the 160 ms hit batch. Candidate
        # acceptance still uses the hit's own much tighter impact-time window.
        self.weapon_track_grace = 0.45
        self.pending_hits = deque()
        # Hold a compact target-local batch long enough for the hit packet and
        # its visual event to arrive in either order.  The live client can put a
        # burn tick about 90 ms ahead of the direct shot in the same update.
        self.pending_hit_grace = 0.16
        self.pending_batch_span = 0.14
        self.pending_hit_timeout = 0.45
        self.pending_hit_limit = 256
        self.pending_attempt_limit = 32
        self.beam_evidence = deque(maxlen=256)
        self.consumed_beam_evidence = {}
        self.beam_evidence_sequence = 0
        self.beam_evidence_grace = 0.35
        # Direct hits and residual effects are different evidence streams.
        # A source stays alive while a confirmed periodic effect is still
        # arriving, without consuming an unrelated projectile or beam.
        self.effect_sources = deque(maxlen=32)
        self.effect_source_lifetime = 8.0
        self.effect_source_reset_gap = 2.0
        self.effect_source_min_delay = 0.20
        self.effect_source_max_fraction = 0.20
        self.effect_source_batch_span = 0.16
        self.effect_source_tick_idle_timeout = 2.25
        self.effect_source_max_stacks = 12

    @staticmethod
    def _empty_direction_stats() -> dict:
        return {
            "total": 0.0,
            "hits": 0,
            "blocked": 0,
            "maximum": 0.0,
            "types": {},
            "targets": {},
        }

    def _reset_encounter_unlocked(self, started_at: float | None) -> None:
        self.encounter_stats = {
            "dealt": self._empty_direction_stats(),
            "received": self._empty_direction_stats(),
        }
        self.encounter_started_at = started_at
        self.encounter_last_hit_at = started_at
        if started_at is None:
            self.encounter_energy_used = 0.0
        else:
            cutoff = float(started_at) - self.energy_prehit_seconds
            while (self.energy_recent_spend
                   and self.energy_recent_spend[0][0] < cutoff):
                self.energy_recent_spend.popleft()
            self.encounter_energy_used = sum(
                amount for _, amount in self.energy_recent_spend)

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

    @staticmethod
    def _report_damage_type(
            server_damage_type: str, attribution: dict | None) -> str:
        """Use a readable burn label while retaining the packet value."""
        if (isinstance(attribution, dict)
                and attribution.get("effect_kind") in (
                    "burn", "possible_burn")):
            return "Fires"
        return server_damage_type

    @staticmethod
    def _weapon_label(event: dict) -> str:
        """Return a supplied weapon name without guessing from the damage type."""
        for field in ("weapon_name", "weapon", "weapon_type", "source_weapon"):
            value = event.get(field)
            if isinstance(value, (dict, list, tuple, set)) or value is None:
                continue
            label = str(value).strip()
            if label:
                if "_" in label:
                    label = label.replace("_", " ").title()
                return label
        return "Unknown"

    @staticmethod
    def _known_weapon_label(label: str | None) -> bool:
        return bool(label and label not in ("Unknown", "—", "-"))

    @classmethod
    def _best_weapon_label(cls, *labels: str | None) -> str:
        for label in labels:
            if cls._known_weapon_label(label):
                return str(label)
        return "Unknown"

    def _entity_weapon_label(self, host: Any, entity_id: Any) -> str:
        """Return a cached active weapon name for the local ship or an entity."""
        row = None
        if self._same_id(entity_id, self._local_player_id(host)):
            finder = getattr(host, "_find_my_entity", None)
            try:
                candidate = finder() if callable(finder) else None
                row = candidate if isinstance(candidate, dict) else None
            except Exception:
                row = None
        if row is None:
            row = self._entity(host, entity_id)
        if not isinstance(row, dict):
            return "Unknown"
        value = row.get("active_weapon_name")
        if isinstance(value, (dict, list, tuple, set)) or value is None:
            return "Unknown"
        label = str(value).strip()
        return label if self._known_weapon_label(label) else "Unknown"

    def _observe_energy(self, host: Any) -> None:
        """Track ship-wide energy spent between authoritative entity updates."""
        finder = getattr(host, "_find_my_entity", None)
        try:
            row = finder() if callable(finder) else None
            row = dict(row) if isinstance(row, dict) else None
        except Exception:
            row = None
        energy = self._number(row, "energy")
        if energy is None:
            return
        maximum = self._number(row, "max_energy")
        regen = self._number(row, "effective_energy_regen")
        if regen is None:
            regen = self._number(row, "energy_output")
        regen = max(0.0, 0.0 if regen is None else regen)
        received_at = self._number(row, "recv_time")
        observed_at = time.monotonic()
        sample_at = observed_at if received_at is None else received_at
        token = (received_at, energy, maximum, regen)

        with self.lock:
            if token == self.energy_observation_token:
                return
            self.energy_observation_token = token
            previous = self.energy_previous
            previous_at = self.energy_previous_at
            previous_max = self.energy_previous_max
            self.energy_previous = energy
            self.energy_previous_at = sample_at
            self.energy_previous_max = maximum

            valid_interval = (
                previous is not None
                and previous_at is not None
                and sample_at > previous_at
                and sample_at - previous_at <= self.energy_sample_max_gap
                and (previous_max is None or maximum is None
                     or math.isclose(previous_max, maximum, rel_tol=1e-6,
                                     abs_tol=0.05))
            )
            if not valid_interval:
                return

            tolerance = max(0.05, (maximum or previous_max or 0.0) * 1e-6)
            both_full = (
                maximum is not None
                and previous >= maximum - tolerance
                and energy >= maximum - tolerance
            )
            spent = 0.0 if both_full else max(
                0.0, previous + regen * (sample_at - previous_at) - energy)
            if spent <= tolerance:
                return

            cutoff = observed_at - self.energy_prehit_seconds
            while (self.energy_recent_spend
                   and self.energy_recent_spend[0][0] < cutoff):
                self.energy_recent_spend.popleft()
            self.energy_recent_spend.append((observed_at, spent))
            started_at = self.encounter_started_at
            if (started_at is not None
                    and observed_at >= float(started_at)):
                self.encounter_energy_used += spent

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
        weapon = self._entity_weapon_label(host, self._local_player_id(host))
        now = time.monotonic()
        expires = now + self.fire_intent_grace
        with self.lock:
            retained = [
                (known_target, known_expiry, known_weapon)
                for known_target, known_expiry, known_weapon in self.fire_intents
                if known_expiry >= now
                and not self._matching_id(known_target, target_id)
            ]
            retained.append((target_id, expires, weapon))
            self.fire_intents = retained[-8:]

    def _recent_local_fire_at(self, target_id: Any) -> str | None:
        now = time.monotonic()
        with self.lock:
            self.fire_intents = [
                (known_target, expiry, weapon)
                for known_target, expiry, weapon in self.fire_intents
                if expiry >= now
            ]
            for known_target, _expiry, weapon in self.fire_intents:
                if self._matching_id(known_target, target_id):
                    return weapon
        return None

    @staticmethod
    def _point_segment_distance(
            px: float, py: float, ax: float, ay: float,
            bx: float, by: float) -> float:
        dx = bx - ax
        dy = by - ay
        length_sq = dx * dx + dy * dy
        if length_sq <= 0.000001:
            return math.hypot(px - ax, py - ay)
        along = ((px - ax) * dx + (py - ay) * dy) / length_sq
        along = max(0.0, min(1.0, along))
        nearest_x = ax + dx * along
        nearest_y = ay + dy * along
        return math.hypot(px - nearest_x, py - nearest_y)

    @staticmethod
    def _target_hint(row: dict) -> Any:
        for field in (
                "target_id", "target_player_id", "target_npc_id",
                "target_entity_id", "asteroid_target_id"):
            value = row.get(field)
            if value is not None:
                return value
        return None

    def _local_owner_tokens(
            self, local_id: Any,
            entity_rows: list[tuple[Any, Any]]) -> set[str]:
        tokens = self._id_tokens(local_id)
        for entity_key, row in entity_rows:
            if not isinstance(row, dict):
                continue
            if not any(
                    self._same_id(row.get(field), local_id)
                    for field in (
                        "owner_id", "player_id", "drone_owner_id",
                        "fighter_owner_id", "rc_owner_id", "source_owner_id")):
                continue
            tokens.update(self._id_tokens(entity_key))
            for field in ("id", "entity_id", "npc_id", "player_id"):
                tokens.update(self._id_tokens(row.get(field)))
        return tokens

    def _remember_projectile_payload(
            self, payload: Any, stream_name: str) -> None:
        """Keep full spawn metadata before the vanilla cache narrows each row."""
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("s", ())
        else:
            rows = ()
        if not isinstance(rows, (list, tuple)):
            return
        now = time.monotonic()
        with self.lock:
            for row in rows:
                if not isinstance(row, dict) or row.get("id") is None:
                    continue
                track_id = stream_name + ":" + str(row["id"])
                previous = self.projectile_payload_metadata.get(track_id)
                merged = (
                    dict(previous["row"])
                    if isinstance(previous, dict)
                    and isinstance(previous.get("row"), dict)
                    else {}
                )
                merged.update(row)
                self.projectile_payload_metadata[track_id] = {
                    "row": merged,
                    "observed_at": now,
                }
            self.projectile_payload_metadata = {
                track_id: metadata
                for track_id, metadata
                in self.projectile_payload_metadata.items()
                if now - float(metadata["observed_at"]) <= 1.0
            }

    def _observe_owned_projectiles(self, host: Any) -> None:
        now = time.monotonic()
        try:
            with host._lock:
                streams = (
                    ("player", dict(getattr(host, "_proj_interp", {}))),
                    ("arena", dict(getattr(host, "_ai_proj_interp", {}))),
                )
                entity_rows = list(
                    getattr(host, "_remote_entities", {}).items())
                entity_rows += list(
                    getattr(host, "_npc_entities", {}).items())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            streams = ()
            entity_rows = []

        local_id = self._local_player_id(host)
        owner_tokens = self._local_owner_tokens(local_id, entity_rows)
        with self.lock:
            raw_metadata = {
                track_id: dict(metadata.get("row", {}))
                for track_id, metadata
                in self.projectile_payload_metadata.items()
            }
        observed = []
        for stream_name, projectiles in streams:
            for projectile_id, row in projectiles.items():
                if not isinstance(row, dict):
                    continue
                track_id = stream_name + ":" + str(projectile_id)
                evidence_row = dict(raw_metadata.get(track_id, {}))
                evidence_row.update(row)
                for field in (
                        "weapon_name", "weapon", "weapon_type",
                        "source_weapon", "hardpoint_index", "turret_index"):
                    if evidence_row.get(field) in (None, ""):
                        raw_value = raw_metadata.get(track_id, {}).get(field)
                        if raw_value not in (None, ""):
                            evidence_row[field] = raw_value
                owner_id = self._attacker_id(evidence_row)
                if not self._id_tokens(owner_id).intersection(owner_tokens):
                    continue
                is_turret = bool(evidence_row.get("is_turret"))
                weapon = self._weapon_label(evidence_row)
                if not self._known_weapon_label(weapon):
                    if is_turret:
                        weapon = self._turret_weapon_label(
                            host, evidence_row)
                    elif not self._same_id(owner_id, local_id):
                        # Drones and fighters have a stable entity weapon.
                        # The local ship does not: its value is merely the last
                        # selected main weapon and cannot name a turret round.
                        weapon = self._entity_weapon_label(host, owner_id)
                try:
                    x = float(row["x"])
                    y = float(row["y"])
                    vx = float(row.get("vx", 0.0))
                    vy = float(row.get("vy", 0.0))
                    radius = max(0.0, float(row.get("radius", 3.0)))
                except (KeyError, TypeError, ValueError):
                    continue
                observed.append((
                    stream_name + ":" + str(projectile_id),
                    x, y, vx, vy, radius, owner_id,
                    self._target_hint(evidence_row), weapon, is_turret,
                    self._event_is_dot_capable(
                        host, evidence_row, weapon, "_projectiles"),
                    dict(evidence_row),
                ))

        with self.lock:
            self.consumed_weapon_tracks = {
                track_id: expiry
                for track_id, expiry in self.consumed_weapon_tracks.items()
                if expiry >= now
            }
            for (track_id, x, y, vx, vy, radius, owner_id,
                 target_hint, weapon, is_turret, dot_capable,
                 evidence_row) in observed:
                if track_id in self.consumed_weapon_tracks:
                    continue
                previous = self.weapon_tracks.get(track_id)
                if previous is None:
                    previous_x, previous_y = x, y
                    previous_seen = now
                elif (x != previous["x"] or y != previous["y"]):
                    previous_x, previous_y = previous["x"], previous["y"]
                    previous_seen = previous["last_seen"]
                else:
                    previous_x = previous["previous_x"]
                    previous_y = previous["previous_y"]
                    previous_seen = previous["previous_seen"]
                self.weapon_tracks[track_id] = {
                    "x": x,
                    "y": y,
                    "previous_x": previous_x,
                    "previous_y": previous_y,
                    "previous_seen": previous_seen,
                    "vx": vx,
                    "vy": vy,
                    "radius": radius,
                    "owner_id": owner_id,
                    "target_hint": target_hint,
                    "weapon": weapon,
                    "is_turret": is_turret,
                    "dot_capable": dot_capable,
                    "evidence": evidence_row,
                    "last_seen": now,
                }
            self.weapon_tracks = {
                track_id: track
                for track_id, track in self.weapon_tracks.items()
                if now - track["last_seen"] <= self.weapon_track_grace
            }

    def _claim_weapon_track(self, track_id: str, now: float) -> bool:
        """Allow one projectile snapshot to name one matching damage event."""
        with self.lock:
            expiry = self.consumed_weapon_tracks.get(track_id)
            if expiry is not None and expiry >= now:
                return False
            self.weapon_tracks.pop(track_id, None)
            self.consumed_weapon_tracks[track_id] = (
                now + self.weapon_track_grace)
            return True

    def _target_geometry(
            self, host: Any,
            target_id: Any) -> tuple[float, float, float] | None:
        row = self._entity(host, target_id)
        position = self._world_position(host, target_id)
        default_radius = 12.0
        if position is None:
            row = self._asteroid(host, target_id)
            position = self._asteroid_position(host, target_id)
            default_radius = 20.0
        if position is None:
            return None
        radius = self._number(row, "radius")
        return position[0], position[1], max(
            1.0, default_radius if radius is None else radius)

    @staticmethod
    def _beam_entries(host: Any) -> tuple[tuple[str, dict, float], ...]:
        entries = []
        for field, lock_field in (
                ("_beams", "_beams_lock"),
                ("_turret_beams", "_turret_beams_lock"),
                ("_station_beams", "_station_beams_lock")):
            lock = getattr(host, lock_field, None)
            try:
                if lock is None:
                    current = list(getattr(host, field, ()))
                else:
                    with lock:
                        current = list(getattr(host, field, ()))
            except (AttributeError, RuntimeError, TypeError):
                continue
            for entry in current:
                if (isinstance(entry, (tuple, list)) and len(entry) >= 2
                        and isinstance(entry[0], dict)):
                    beam, born_at = entry[0], entry[1]
                elif isinstance(entry, dict):
                    beam, born_at = entry, time.monotonic()
                else:
                    continue
                try:
                    entries.append((field, beam, float(born_at)))
                except (TypeError, ValueError):
                    continue
        return tuple(entries)

    def _remember_beam_event(
            self, source: str, beam: Any,
            born_at: float | None = None) -> None:
        if not isinstance(beam, dict):
            return
        captured = dict(beam)
        host = self.host
        if (source == "_beams" and host is not None
                and self._same_id(
                    self._attacker_id(captured),
                    self._local_player_id(host))):
            # The ordinary local beam stream does not carry a weapon name.
            # Preserve the selected weapon at fire time so a later selection
            # change cannot relabel this specific beam.
            weapon = self._entity_weapon_label(
                host, self._local_player_id(host))
            if self._known_weapon_label(weapon):
                captured["_damage_numbers_local_weapon"] = weapon
        observed_at = time.monotonic() if born_at is None else float(born_at)
        with self.lock:
            self.beam_evidence_sequence += 1
            evidence_id = ("captured", self.beam_evidence_sequence)
            self.beam_evidence.append(
                (source, captured, observed_at, evidence_id))

    def _cached_beam_entries(self, now: float) -> tuple:
        with self.lock:
            retained = [
                entry for entry in self.beam_evidence
                if -0.1 <= now - entry[2] <= self.beam_evidence_grace
            ]
            self.beam_evidence = deque(retained, maxlen=256)
            self.consumed_beam_evidence = {
                key: expiry
                for key, expiry in self.consumed_beam_evidence.items()
                if expiry >= now
            }
            return tuple(retained)

    def _claim_beam_evidence(self, evidence_id: Any, now: float) -> bool:
        with self.lock:
            expiry = self.consumed_beam_evidence.get(evidence_id)
            if expiry is not None and expiry >= now:
                return False
            self.consumed_beam_evidence[evidence_id] = (
                now + self.beam_evidence_grace)
            return True

    def _turret_hardpoints(
            self, host: Any, event: dict) -> tuple[dict, ...]:
        """Return the fitted hardpoints that can have produced an event."""
        state = getattr(host, "_turret_state", None)
        hardpoints = state.get("hardpoints") if isinstance(state, dict) else None
        if not isinstance(hardpoints, (list, tuple)):
            return ()
        hardpoint_index = None
        for field in ("hardpoint_index", "turret_index"):
            value = event.get(field)
            if value is not None:
                hardpoint_index = value
                break

        candidates = []
        for ordinal, hardpoint in enumerate(hardpoints):
            if not isinstance(hardpoint, dict) or not hardpoint.get("weapon_type"):
                continue
            if hardpoint_index is None:
                candidates.append(hardpoint)
                continue
            if self._same_id(hardpoint.get("index", ordinal), hardpoint_index):
                candidates = [hardpoint]
                break
        return tuple(candidates)

    def _hardpoint_weapon_label(self, hardpoint: dict) -> str:
        definition = hardpoint.get("weapon_def")
        if isinstance(definition, dict):
            return self._weapon_label({
                "weapon_name": definition.get("display_name"),
                "weapon_type": hardpoint.get("weapon_type"),
            })
        return self._weapon_label(hardpoint)

    def _hardpoint_damage_type(self, hardpoint: dict) -> str:
        definition = hardpoint.get("weapon_def")
        rows = (hardpoint, definition) if isinstance(definition, dict) else (hardpoint,)
        for row in rows:
            label = self._damage_type_label(row)
            if label != "Unknown":
                return label
        return "Unknown"

    def _hardpoint_expected_damage(self, hardpoint: dict) -> float | None:
        definition = hardpoint.get("weapon_def")
        rows = (hardpoint, definition) if isinstance(definition, dict) else (hardpoint,)
        for row in rows:
            for field in ("effective_damage", "damage", "base_damage"):
                value = self._number(row, field)
                if value is not None and value > 0.0:
                    return value
        return None

    def _compatible_hardpoints_for_hit(
            self, hardpoints: tuple[dict, ...] | list[dict],
            hit: dict) -> tuple[tuple[dict, ...], bool]:
        """Narrow anonymous turrets by type, never by applied damage.

        The hit amount is post-mitigation and can legitimately approach zero.
        If several fitted turret names share a type, the caller leaves the
        result unknown instead of guessing from their raw weapon damage.
        """
        hit_type = self._damage_type_label(hit)
        signature_known = False
        compatible = []
        for hardpoint in hardpoints:
            damage_type = self._hardpoint_damage_type(hardpoint)
            signature_known = signature_known or damage_type != "Unknown"
            if (hit_type != "Unknown" and damage_type != "Unknown"
                    and hit_type != damage_type):
                continue
            compatible.append(hardpoint)
        return tuple(compatible), signature_known

    def _turret_weapon_evidence(
            self, host: Any, event: dict,
            hit: dict | None = None) -> dict | None:
        """Resolve a turret without ever consulting the selected main weapon."""
        supplied = self._weapon_label(event)
        candidates = self._turret_hardpoints(host, event)
        has_exact_index = any(
            event.get(field) is not None
            for field in ("hardpoint_index", "turret_index")
        )
        if not candidates:
            return (
                {"weapon": supplied, "hardpoints": ()}
                if self._known_weapon_label(supplied) else None
            )

        if self._known_weapon_label(supplied):
            named = tuple(
                hardpoint for hardpoint in candidates
                if self._hardpoint_weapon_label(hardpoint).casefold()
                == supplied.casefold()
            )
            # A supplied weapon is direct event evidence.  Applied damage can
            # differ substantially after resistance, flat mitigation or crits.
            return {
                "weapon": supplied,
                "hardpoints": named or candidates,
            }

        if has_exact_index:
            labels = {
                self._hardpoint_weapon_label(hardpoint)
                for hardpoint in candidates
            }
            labels.discard("Unknown")
            if len(labels) == 1:
                return {
                    "weapon": labels.pop(),
                    "hardpoints": candidates,
                }
            return None

        usable = list(candidates)
        if isinstance(hit, dict):
            compatible, signature_known = (
                self._compatible_hardpoints_for_hit(candidates, hit))
            if compatible:
                usable = list(compatible)
            elif signature_known:
                # A source-free 45/90 burn tick must not spend a nearby
                # 5,622-damage Patriot beam merely because its timing overlaps.
                return None

        labels = {
            self._hardpoint_weapon_label(hardpoint)
            for hardpoint in usable
        }
        labels.discard("Unknown")
        if len(labels) != 1:
            return None
        return {"weapon": labels.pop(), "hardpoints": tuple(usable)}

    def _turret_weapon_label(self, host: Any, event: dict) -> str:
        """Resolve a nameless turret event through its reported hardpoint."""
        evidence = self._turret_weapon_evidence(host, event)
        return "Unknown" if evidence is None else str(evidence["weapon"])

    @staticmethod
    def _truthy_effect_value(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return math.isfinite(float(value)) and float(value) > 0.0
        if value is None or isinstance(value, (dict, list, tuple, set)):
            return False
        return str(value).strip().casefold() not in ("", "0", "false", "none")

    def _event_is_dot_capable(
            self, host: Any, event: dict,
            weapon: str, source: str = "") -> bool:
        """Recognise explicit burn metadata, with a narrow name fallback."""
        rows = [event]
        if source == "_turret_beams" or bool(event.get("is_turret")):
            for hardpoint in self._turret_hardpoints(host, event):
                label = self._hardpoint_weapon_label(hardpoint)
                if (self._known_weapon_label(weapon)
                        and self._known_weapon_label(label)
                        and label != weapon):
                    continue
                rows.append(hardpoint)
                definition = hardpoint.get("weapon_def")
                if isinstance(definition, dict):
                    rows.append(definition)
        marker_fields = (
            "is_dot", "applies_dot", "damage_over_time", "dot_damage",
            "dot_dps", "dot_hits", "dot_duration", "burn_damage",
            "burn_dps", "burn_ticks", "burn_duration", "ignites",
            "ignite_chance", "sets_on_fire",
        )
        for row in rows:
            if any(
                    field in row
                    and self._truthy_effect_value(row.get(field))
                    for field in marker_fields):
                return True
        text = " ".join(
            str(value)
            for row in rows
            for field in (
                "display_name", "name", "description",
                "effect_name", "extra_effect_name")
            for value in (row.get(field),)
            if value is not None
        )
        text = (str(weapon or "") + " " + text).casefold()
        return any(marker in text for marker in (
            "blaze", "inferno", "flame", "incendiary",
            "fire cannon", "sets targets alight", "burning", "burn damage",
        ))

    def _owned_beam_candidates(
            self, host: Any, target_id: Any,
            event_at: float, hit: dict) -> list[dict]:
        geometry = self._target_geometry(host, target_id)
        if geometry is None:
            return []
        target_x, target_y, target_radius = geometry
        now = time.monotonic()
        cached = self._cached_beam_entries(now)
        hooked_sources = {
            source for method_name, source in (
                ("add_beam", "_beams"),
                ("add_turret_beam", "_turret_beams"),
                ("add_station_beam", "_station_beams"))
            if method_name in self.event_wrappers
        }
        live = tuple(
            (source, beam, born_at,
             ("live", source, id(beam), round(born_at, 6)))
            for source, beam, born_at in self._beam_entries(host)
            if source not in hooked_sources
        )
        candidates = []
        for source, beam, born_at, evidence_id in cached + live:
            delta = float(event_at) - born_at
            if delta < -0.08 or delta > 0.30:
                continue
            with self.lock:
                if self.consumed_beam_evidence.get(evidence_id, -1.0) >= now:
                    continue
            owner_id = self._attacker_id(beam)
            if not self._owned_by_player(host, owner_id):
                continue
            weapon = self._weapon_label(beam)
            if (source == "_beams"
                    and self._same_id(
                        owner_id, self._local_player_id(host))):
                captured_weapon = beam.get("_damage_numbers_local_weapon")
                if isinstance(captured_weapon, str):
                    weapon = self._best_weapon_label(
                        weapon, captured_weapon.strip())
            if source == "_turret_beams":
                # A turret event belongs to its hardpoint, never to the local
                # ship's currently selected main weapon.
                turret_evidence = self._turret_weapon_evidence(
                    host, beam, hit)
                if turret_evidence is None:
                    continue
                weapon = str(turret_evidence["weapon"])
            elif (not self._known_weapon_label(weapon)
                  and not self._same_id(
                      owner_id, self._local_player_id(host))):
                weapon = self._entity_weapon_label(host, owner_id)
            dot_capable = self._event_is_dot_capable(
                host, beam, weapon, source)
            target_hint = self._target_hint(beam)
            if self._matching_id(target_hint, target_id):
                candidates.append({
                    "score": abs(delta) * 3.0,
                    "observed_at": born_at,
                    "evidence_kind": "beam",
                    "evidence_id": evidence_id,
                    "weapon": weapon,
                    "dot_capable": dot_capable,
                })
                continue
            try:
                end_x = float(beam["ex"])
                end_y = float(beam["ey"])
            except (KeyError, TypeError, ValueError):
                continue
            padding = max(14.0, target_radius * 0.35)
            endpoint_distance = math.hypot(
                end_x - target_x, end_y - target_y)
            if endpoint_distance <= target_radius + padding:
                score = (endpoint_distance / max(target_radius + padding, 1.0)
                         + abs(delta) * 3.0)
                candidates.append({
                    "score": score,
                    "observed_at": born_at,
                    "evidence_kind": "beam",
                    "evidence_id": evidence_id,
                    "weapon": weapon,
                    "dot_capable": dot_capable,
                })
        return candidates

    def _owned_projectile_candidates(
            self, host: Any, target_id: Any,
            event_at: float, hit: dict, *,
            refresh_projectiles: bool = True) -> list[dict]:
        if refresh_projectiles:
            self._observe_owned_projectiles(host)
        geometry = self._target_geometry(host, target_id)
        if geometry is None:
            return []
        target_x, target_y, target_radius = geometry
        now = time.monotonic()
        with self.lock:
            tracks = tuple(self.weapon_tracks.items())
        candidates = []
        for track_id, track in tracks:
            observed_at = min(
                (float(track.get("last_seen", event_at)),
                 float(track.get("previous_seen", event_at))),
                key=lambda value: abs(float(event_at) - value),
            )
            delta = float(event_at) - observed_at
            if delta < -0.12 or delta > 0.35:
                continue
            with self.lock:
                if self.consumed_weapon_tracks.get(track_id, -1.0) >= now:
                    continue
            weapon = str(track.get("weapon") or "Unknown")
            dot_capable = bool(track.get("dot_capable"))
            if bool(track.get("is_turret")):
                event = track.get("evidence")
                event = event if isinstance(event, dict) else {}
                turret_evidence = self._turret_weapon_evidence(
                    host, event, hit)
                if turret_evidence is None:
                    continue
                weapon = str(turret_evidence["weapon"])
                dot_capable = self._event_is_dot_capable(
                    host, event, weapon, "_projectiles")
            if self._matching_id(track["target_hint"], target_id):
                candidates.append({
                    "score": abs(delta) * 3.0,
                    "observed_at": observed_at,
                    "evidence_kind": "projectile",
                    "evidence_id": track_id,
                    "weapon": weapon,
                    "dot_capable": dot_capable,
                })
                continue
            # The client normally receives projectile corrections at 10 Hz.
            # Only bridge a very short gap beyond the newest correction; a
            # longer prediction can catch a different weapon fired later.
            prediction = 0.10
            end_x = track["x"] + track["vx"] * prediction
            end_y = track["y"] + track["vy"] * prediction
            distance = self._point_segment_distance(
                target_x, target_y,
                track["previous_x"], track["previous_y"],
                end_x, end_y,
            )
            tolerance = (target_radius + track["radius"]
                         + max(2.0, min(8.0, target_radius * 0.10)))
            if distance > tolerance:
                continue
            start_distance = math.hypot(
                track["previous_x"] - target_x,
                track["previous_y"] - target_y)
            end_distance = math.hypot(end_x - target_x, end_y - target_y)
            current_distance = math.hypot(
                track["x"] - target_x, track["y"] - target_y)
            if (current_distance > tolerance
                    and end_distance > start_distance
                    + max(0.2, tolerance * 0.02)):
                continue
            score = (distance / tolerance
                     + abs(delta) * 3.0
                     + current_distance / max(tolerance * 8.0, 1.0))
            candidates.append({
                "score": score,
                "observed_at": observed_at,
                "evidence_kind": "projectile",
                "evidence_id": track_id,
                "weapon": weapon,
                "dot_capable": dot_capable,
            })
        return candidates

    def _owned_weapon_candidates(
            self, host: Any, target_id: Any,
            event_at: float, hit: dict, *,
            refresh_projectiles: bool = True) -> list[dict]:
        candidates = self._owned_beam_candidates(
            host, target_id, event_at, hit)
        candidates += self._owned_projectile_candidates(
            host, target_id, event_at, hit,
            refresh_projectiles=refresh_projectiles)
        return candidates

    def _recent_owned_weapon_at(
            self, host: Any, target_id: Any,
            event_at: float, hit: dict,
            candidates: list[dict] | None = None) -> dict | None:
        # Candidate collection is side-effect free. Only the single overall
        # winner is claimed, so one hit cannot silently consume both a beam and
        # a projectile that should identify the next hit.
        if candidates is None:
            candidates = self._owned_weapon_candidates(
                host, target_id, event_at, hit)
        ordered = sorted(
            candidates,
            key=lambda candidate: (
                float(candidate["score"]),
                abs(float(event_at) - float(candidate["observed_at"])),
                -float(candidate["observed_at"]),
            ),
        )
        now = time.monotonic()
        for candidate in ordered:
            if candidate["evidence_kind"] == "beam":
                claimed = self._claim_beam_evidence(
                    candidate["evidence_id"], now)
            else:
                claimed = self._claim_weapon_track(
                    candidate["evidence_id"], now)
            if claimed:
                return candidate
        return None

    def _pending_direct_evidence_key(
            self, item: dict, candidates: list[dict]) -> tuple:
        """Order a hit by constrained, close visual evidence—not magnitude."""
        hit = item["hit"]
        unique_evidence = {
            (candidate["evidence_kind"], str(candidate["evidence_id"]))
            for candidate in candidates
        }
        if candidates:
            best_score = min(float(candidate["score"])
                             for candidate in candidates)
            missing_evidence = 0
            candidate_count = len(unique_evidence)
        else:
            best_score = float("inf")
            missing_evidence = 1
            candidate_count = 0
        return (
            self._hit_declares_dot(hit),
            missing_evidence,
            candidate_count,
            best_score,
            float(item["event_at"]),
            -(self._damage(hit) or 0.0),
        )

    @staticmethod
    def _hit_declares_dot(hit: dict) -> bool:
        for field in (
                "is_dot", "dot", "damage_over_time", "is_burn",
                "burn", "periodic", "is_periodic"):
            if field in hit and _DamageState._truthy_effect_value(
                    hit.get(field)):
                return True
        for field in ("damage_kind", "effect_type", "effect", "hit_kind"):
            value = str(hit.get(field, "")).strip().casefold()
            if any(marker in value for marker in (
                    "dot", "damage_over_time", "burn", "fire_tick",
                "periodic")):
                return True
        return False

    @staticmethod
    def _effect_source_activity_at(source: dict) -> float:
        """Return the most recent direct hit or accepted residual tick."""
        observed = []
        for field in ("last_direct_at", "last_tick_at"):
            value = source.get(field)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                observed.append(float(value))
        return max(observed) if observed else -math.inf

    def _remember_effect_source(
            self, target_id: Any, attacker_id: Any,
            match: dict, amount: float | None,
            damage_type: str, event_at: float,
            channel: str | None = None) -> None:
        if (match.get("evidence_kind") not in ("beam", "projectile", "hit")
                or not match.get("dot_capable")
                or not self._known_weapon_label(match.get("weapon"))
                or amount is None or amount <= 0.0):
            return
        weapon = str(match["weapon"])
        current = float(event_at)
        with self.lock:
            retained = deque(maxlen=self.effect_sources.maxlen)
            updated = False
            for source in self.effect_sources:
                if float(source["expires_at"]) < current:
                    continue
                if (not updated
                        and self._matching_id(
                            source.get("target_id"), target_id)
                        and source.get("weapon") == weapon
                        and (channel is None
                             or source.get("channel") is None
                             or source.get("channel") == channel)):
                    source = dict(source)
                    previous_activity_at = self._effect_source_activity_at(
                        source)
                    source["attacker_id"] = attacker_id
                    source["channel"] = channel
                    source["last_direct_at"] = current
                    source["expires_at"] = (
                        current + self.effect_source_lifetime)
                    if (current - previous_activity_at
                            > self.effect_source_reset_gap):
                        source["first_direct_at"] = current
                        source["direct_damage"] = float(amount)
                        source["tick_samples"] = []
                        source["confirmed"] = False
                        source["last_tick_at"] = None
                        source["direct_batch_at"] = current
                        source["direct_batch_count"] = 1
                        source["stack_ceiling"] = 1
                    else:
                        prior = float(source.get("direct_damage", amount))
                        source["direct_damage"] = min(prior, float(amount))
                        batch_at = float(source.get("direct_batch_at", current))
                        if 0.0 <= current - batch_at <= self.effect_source_batch_span:
                            batch_count = int(
                                source.get("direct_batch_count", 1)) + 1
                        else:
                            batch_at = current
                            batch_count = 1
                        source["direct_batch_at"] = batch_at
                        source["direct_batch_count"] = batch_count
                        source["stack_ceiling"] = max(
                            int(source.get("stack_ceiling", 1)), batch_count)
                    source["damage_type"] = damage_type
                    updated = True
                retained.append(source)
            if not updated:
                retained.append({
                    "target_id": target_id,
                    "attacker_id": attacker_id,
                    "weapon": weapon,
                    "damage_type": damage_type,
                    "channel": channel,
                    "direct_damage": float(amount),
                    "first_direct_at": current,
                    "last_direct_at": current,
                    "expires_at": current + self.effect_source_lifetime,
                    "tick_samples": [],
                    "confirmed": False,
                    "last_tick_at": None,
                    "direct_batch_at": current,
                    "direct_batch_count": 1,
                    "stack_ceiling": 1,
                })
            self.effect_sources = retained

    def _effect_candidate_limit(self, source: dict) -> float:
        """Bound anonymous ticks by the observed simultaneous Blaze burst."""
        direct_damage = float(source.get("direct_damage", 0.0))
        stack_ceiling = max(1, min(
            int(source.get("stack_ceiling", 1)),
            self.effect_source_max_stacks,
        ))
        return direct_damage * self.effect_source_max_fraction * stack_ceiling

    def _stacked_tick_amounts_compatible(
            self, previous: float, current: float) -> bool:
        """Allow a confirmed burn to gain or lose whole stacks between ticks."""
        if previous <= 0.0 or current <= 0.0:
            return False
        ratio = current / previous
        for prior_stacks in range(1, self.effect_source_max_stacks + 1):
            for current_stacks in range(1, self.effect_source_max_stacks + 1):
                expected = current_stacks / prior_stacks
                if math.isclose(ratio, expected, rel_tol=0.04, abs_tol=0.025):
                    return True
        return False

    def _recent_effect_match(
            self, hit: dict, target_id: Any,
            attacker_id: Any, event_at: float,
            channel: str | None = None) -> dict | None:
        """Return a bounded burn candidate without spending direct-hit evidence."""
        amount = self._damage(hit)
        if amount is None:
            return None
        explicit = self._hit_declares_dot(hit)
        current = float(event_at)
        with self.lock:
            retained = deque(maxlen=self.effect_sources.maxlen)
            matches = []
            for source in self.effect_sources:
                if float(source["expires_at"]) < current:
                    continue
                retained.append(source)
                if not self._matching_id(source.get("target_id"), target_id):
                    continue
                source_channel = source.get("channel")
                if (channel is not None and source_channel is not None
                        and channel != source_channel):
                    continue
                source_attacker = source.get("attacker_id")
                if (attacker_id is not None and source_attacker is not None
                        and not self._matching_id(
                            attacker_id, source_attacker)):
                    continue
                elapsed = current - float(source["last_direct_at"])
                if elapsed < -self.pending_batch_span:
                    continue
                if (0.0 <= elapsed < self.effect_source_min_delay
                        and not explicit
                        and not bool(source.get("confirmed"))):
                    continue
                if not explicit:
                    if amount > self._effect_candidate_limit(source):
                        continue
                matches.append(source)
            self.effect_sources = retained
            if not matches:
                return None

            source = max(
                matches, key=lambda value: float(value["last_direct_at"]))
            samples = list(source.get("tick_samples", ()))
            duplicate = bool(
                samples
                and math.isclose(
                    float(samples[-1][0]), current, abs_tol=1e-6)
                and math.isclose(
                    float(samples[-1][1]), amount,
                    rel_tol=1e-6, abs_tol=1e-6)
            )
            if not duplicate:
                interval = None
                periodic = False
                if samples:
                    interval = current - float(samples[-1][0])
                    prior_amount = float(samples[-1][1])
                    periodic = (
                        0.45 <= interval <= 1.55
                        and self._stacked_tick_amounts_compatible(
                            prior_amount, amount))
                    if periodic:
                        source["confirmed"] = True
                if bool(source.get("confirmed")) and samples and not periodic:
                    # Once the cadence is known, do not let another low,
                    # source-free hit inherit the burn label.
                    return None
                samples.append((current, float(amount)))
                source["tick_samples"] = samples[-4:]
                source["last_tick_at"] = current
                if bool(source.get("confirmed")):
                    source["expires_at"] = (
                        current + self.effect_source_tick_idle_timeout)
            confirmed = bool(source.get("confirmed")) or explicit
            return {
                "weapon": str(source["weapon"]),
                "dot_capable": True,
                # Source-free packets cannot prove the first residual belongs
                # to us.  Promote it only after a stable tick cadence appears.
                "effect_kind": (
                    "burn" if confirmed else "possible_burn"),
                "evidence_kind": "effect",
            }

    def _owned_by_player(self, host: Any, entity_id: Any) -> bool:
        local_id = self._local_player_id(host)
        if self._same_id(entity_id, local_id):
            return True
        row = self._entity(host, entity_id)
        if not isinstance(row, dict):
            return False
        for field in (
                "owner_id", "player_id", "drone_owner_id", "rc_owner_id",
                "fighter_owner_id", "source_owner_id"):
            if self._same_id(row.get(field), local_id):
                return True
        return False

    def _combat_direction(
            self, host: Any, hit: dict,
            target_id: Any, event_at: float, *,
            allow_visual: bool = True,
            channel: str | None = None,
            visual_candidates: list[dict] | None = None,
            ) -> tuple[str, Any, str, dict | None] | None:
        attacker_id = self._attacker_id(hit)
        weapon = self._weapon_label(hit)
        if self._same_id(target_id, self._local_player_id(host)):
            if (attacker_id is None
                    and channel != "DUNGEON_AI_HIT_ENTITY "):
                attacker_id = hit.get("entity_id")
            return "received", attacker_id, weapon, None

        if self._known_weapon_label(weapon):
            attribution = {
                "weapon": weapon,
                "dot_capable": self._event_is_dot_capable(
                    host, hit, weapon, "hit"),
                "effect_kind": None,
                "evidence_kind": "hit",
            }
            if self._owned_by_player(host, attacker_id):
                return "dealt", attacker_id, weapon, attribution

        owned_attacker = self._owned_by_player(host, attacker_id)
        if owned_attacker:
            if not self._same_id(
                    attacker_id, self._local_player_id(host)):
                entity_weapon = self._entity_weapon_label(host, attacker_id)
                if self._known_weapon_label(entity_weapon):
                    attribution = {
                        "weapon": entity_weapon,
                        "dot_capable": self._event_is_dot_capable(
                            host, hit, entity_weapon, "entity"),
                        "effect_kind": None,
                        "evidence_kind": "entity",
                    }
                    return "dealt", attacker_id, entity_weapon, attribution
            # Real protocol hits get one compact correlation window. Direct
            # callers without channel context still retain correct totals, but
            # deliberately show Unknown rather than the selected main weapon.
            if not allow_visual:
                if channel is not None:
                    return None
                return "dealt", attacker_id, "Unknown", None
        if not allow_visual:
            return None

        # Spend direct beam/projectile evidence before considering a residual
        # effect.  Otherwise a low but valid Patriot hit can be stolen by an
        # active Blaze context and shift every later turret label.
        match = self._recent_owned_weapon_at(
            host, target_id, event_at, hit, visual_candidates)
        if match is not None:
            if self._known_weapon_label(weapon):
                match = dict(match)
                match["weapon"] = weapon
                match["dot_capable"] = self._event_is_dot_capable(
                    host, hit, weapon, "hit")
            return (
                "dealt", attacker_id or self._local_player_id(host),
                str(match["weapon"]), match,
            )

        effect = self._recent_effect_match(
            hit, target_id, attacker_id, event_at, channel)
        if effect is not None:
            return (
                "dealt", attacker_id or self._local_player_id(host),
                str(effect["weapon"]), effect,
            )
        if owned_attacker:
            return "dealt", attacker_id, "Unknown", None
        return None

    def _defer_hit(
            self, kind: str, hit: dict, event_at: float,
            channel: str | None = None) -> None:
        pending = {
            "kind": kind,
            "hit": dict(hit),
            "event_at": float(event_at),
            "channel": channel,
        }
        with self.lock:
            self.pending_hits.append(pending)
            while len(self.pending_hits) > self.pending_hit_limit:
                self.pending_hits.popleft()

    def _resolve_pending_hits(self, host: Any) -> None:
        now = time.monotonic()
        # Observe once per frame here, even with no pending hits, so brief
        # projectiles are retained without rescanning for every candidate.
        self._observe_owned_projectiles(host)
        with self.lock:
            pending = sorted(
                self.pending_hits,
                key=lambda item: float(item["event_at"]),
            )
            self.pending_hits.clear()
        retained = []
        attempted = 0
        while pending:
            anchor = pending.pop(0)
            anchor_hit = anchor["hit"]
            anchor_target = anchor_hit.get(
                "target_id", anchor_hit.get("id"))
            anchor_key = (
                anchor["kind"], str(anchor_target), anchor.get("channel"))
            batch = [anchor]
            remaining = []
            batch_end = float(anchor["event_at"]) + self.pending_batch_span
            for item in pending:
                hit = item["hit"]
                target_id = hit.get("target_id", hit.get("id"))
                item_key = (item["kind"], str(target_id), item.get("channel"))
                if item_key == anchor_key and float(item["event_at"]) <= batch_end:
                    batch.append(item)
                else:
                    remaining.append(item)
            pending = remaining

            anchor_age = now - float(anchor["event_at"])
            if anchor_age < self.pending_hit_grace:
                retained.extend(batch)
                continue

            available = self.pending_attempt_limit - attempted
            if available <= 0:
                retained.extend(batch)
                continue
            if len(batch) > available:
                retained.extend(batch[available:])
                batch = batch[:available]

            # Assign the most constrained and closest visual evidence first.
            # Damage is post-mitigation, so even a direct turret hit can be
            # smaller than a DOT tick and must not lose its beam for that reason.
            candidate_cache = {}
            priorities = {}
            for item in batch:
                hit = item["hit"]
                target_id = hit.get("target_id", hit.get("id"))
                candidates = self._owned_weapon_candidates(
                    host, target_id, float(item["event_at"]), hit,
                    refresh_projectiles=False)
                candidate_cache[id(item)] = candidates
                priorities[id(item)] = self._pending_direct_evidence_key(
                    item, candidates)
            batch.sort(key=lambda item: priorities[id(item)])
            for item in batch:
                age = now - float(item["event_at"])
                if attempted >= self.pending_attempt_limit:
                    retained.append(item)
                    continue
                attempted += 1
                if item["kind"] == "asteroid":
                    resolved = self.record_asteroid_hit(
                        host, item["hit"], allow_defer=False,
                        event_at=item["event_at"],
                        channel=item.get("channel"),
                        visual_candidates=candidate_cache[id(item)])
                else:
                    resolved = self.record_ship_hit(
                        host, item["hit"], allow_defer=False,
                        event_at=item["event_at"],
                        channel=item.get("channel"),
                        visual_candidates=candidate_cache[id(item)])
                if resolved:
                    continue
                if age <= self.pending_hit_timeout:
                    retained.append(item)
                    continue
                self.api.logger.debug(
                    "DAMAGE_EVENT_UNRESOLVED channel=%s target=%s "
                    "attacker=%s fields=%s",
                    item.get("channel"),
                    item["hit"].get(
                        "target_id", item["hit"].get("id")),
                    self._attacker_id(item["hit"]),
                    sorted(item["hit"]))
        if retained:
            with self.lock:
                combined = sorted(
                    retained + list(self.pending_hits),
                    key=lambda item: float(item["event_at"]),
                )
                self.pending_hits = deque(
                    combined[-self.pending_hit_limit:])

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
            damage_type: str, weapon: str = "Unknown",
            event_at: float | None = None,
            server_damage_type: str | None = None) -> None:
        local_hit = direction == "received"
        target_label = self._target_label(host, target_id, kind)
        attacker_label = (
            self._target_label(host, attacker_id, "ship")
            if attacker_id is not None else ""
        )
        display_label = target_label
        if local_hit and attacker_label and attacker_label != "Player":
            display_label = attacker_label
        now = time.monotonic() if event_at is None else float(event_at)
        entry = {
            "when": now,
            "direction": direction,
            "target": display_label,
            "attacker": attacker_label,
            "damage_type": damage_type,
            "server_damage_type": server_damage_type or damage_type,
            "weapon": weapon,
            "amount": float(amount),
            "blocked": amount == 0.0,
        }
        with self.lock:
            if self.encounter_started_at is None:
                self._reset_encounter_unlocked(now)
            if (self.encounter_last_hit_at is None
                    or now >= self.encounter_last_hit_at):
                self.encounter_last_hit_at = now
            encounter = self.encounter_stats[direction]
            encounter["total"] += amount
            encounter["hits"] += 1
            encounter["blocked"] += int(amount == 0.0)
            encounter["maximum"] = max(encounter["maximum"], amount)
            for totals, key in (
                    (encounter["types"], damage_type),
                    (encounter["targets"], display_label)):
                bucket = totals.setdefault(key, {"amount": 0.0, "hits": 0})
                bucket["amount"] += amount
                bucket["hits"] += 1
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
            self.pending_hits.clear()
            self.beam_evidence.clear()
            self.consumed_beam_evidence.clear()
            self.weapon_tracks.clear()
            self.projectile_payload_metadata.clear()
            self.consumed_weapon_tracks.clear()
            self.effect_sources.clear()
            self.feed.clear()
            for rows in self.feed_by_direction.values():
                rows.clear()
            for tab in self.feed_scroll:
                self.feed_scroll[tab] = 0
            self.dealt_total = 0.0
            self.received_total = 0.0
            self.dealt_hits = 0
            self.received_hits = 0
            self._reset_encounter_unlocked(None)
            self.energy_recent_spend.clear()
            self.energy_previous = None
            self.energy_previous_at = None
            self.energy_previous_max = None
            self.energy_observation_token = None

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

    @staticmethod
    def _direction_stats(source: dict, duration: float) -> dict:
        total = float(source["total"])
        hits = int(source["hits"])
        def ranked(source: dict[str, dict[str, float | int]]) -> tuple[dict, ...]:
            result = []
            for label, values in source.items():
                amount = float(values["amount"])
                result.append({
                    "label": label,
                    "amount": amount,
                    "hits": int(values["hits"]),
                    "percent": (amount / total * 100.0) if total > 0.0 else 0.0,
                })
            result.sort(key=lambda value: (-value["amount"], value["label"]))
            return tuple(result)

        return {
            "total": total,
            "hits": hits,
            "blocked": int(source["blocked"]),
            "average": total / hits if hits else 0.0,
            "maximum": float(source["maximum"]),
            "dps": total / max(1.0, duration) if hits else 0.0,
            "hits_per_second": hits / max(1.0, duration) if hits else 0.0,
            "types": ranked(source["types"]),
            "targets": ranked(source["targets"]),
        }

    def combat_stats_snapshot(self, now: float | None = None) -> dict:
        with self.lock:
            started = self.encounter_started_at
            last = self.encounter_last_hit_at
            if started is None or last is None:
                status = "idle"
                duration = 0.0
            else:
                status = "running"
                duration = max(0.0, float(last) - float(started))
            energy_used = max(0.0, float(self.encounter_energy_used))
            dealt_total = float(self.encounter_stats["dealt"]["total"])
            dpe_available = energy_used > 1e-6
            return {
                "status": status,
                "duration": duration,
                "dealt": self._direction_stats(
                    self.encounter_stats["dealt"], duration),
                "received": self._direction_stats(
                    self.encounter_stats["received"], duration),
                "energy_used": energy_used,
                "dpe": dealt_total / energy_used if dpe_available else 0.0,
                "dpe_available": dpe_available,
                "dpe_reason": (
                    "" if dpe_available else "No session energy use recorded"),
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
        weapon: str = "Unknown",
        event_at: float | None = None,
        server_damage_type: str | None = None,
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
            damage_type, weapon, event_at,
            server_damage_type=server_damage_type)
        self.api.logger.debug(
            "DAMAGE_EVENT_RECORDED target=%s attacker=%s amount=%.3f direction=%s",
            target_id, attacker_id, amount, direction)
        if position is None:
            return
        colour = (225, 230, 235) if local_hit else (255, 90, 80)
        item = (
            position[0], position[1],
            ("0" if amount == 0.0 else f"-{amount:.0f}"), colour,
            time.monotonic() if event_at is None else float(event_at),
            not local_hit,
        )
        with self.lock:
            self.items.append(item)
            if len(self.items) > self.limit:
                del self.items[:-self.limit]

    def record_ship_hit(
            self, host: Any, hit: Any, *, allow_defer: bool = True,
            event_at: float | None = None,
            channel: str | None = None,
            visual_candidates: list[dict] | None = None) -> bool:
        if not isinstance(hit, dict):
            return False
        target_id = hit.get("target_id")
        if target_id is None:
            return False
        occurred_at = time.monotonic() if event_at is None else float(event_at)
        combat = self._combat_direction(
            host, hit, target_id, occurred_at,
            allow_visual=not allow_defer, channel=channel,
            visual_candidates=visual_candidates)
        if combat is None:
            if allow_defer:
                self._defer_hit("ship", hit, occurred_at, channel)
            return False
        direction, attacker_id, weapon, attribution = combat
        amount = self._damage(hit)
        server_damage_type = self._damage_type_label(hit)
        damage_type = self._report_damage_type(
            server_damage_type, attribution)
        if isinstance(attribution, dict):
            if attribution.get("effect_kind") == "burn":
                weapon = f"{weapon} (Burn)"
            elif attribution.get("effect_kind") == "possible_burn":
                weapon = f"{weapon} (Possible Burn)"
            elif direction == "dealt":
                self._remember_effect_source(
                    target_id, attacker_id, attribution, amount,
                    server_damage_type, occurred_at, channel)
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
            return True
        if amount is None and remaining is None:
            return True
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
            damage_type=damage_type, weapon=weapon,
            event_at=occurred_at,
            server_damage_type=server_damage_type)
        return True

    def record_asteroid_hit(
            self, host: Any, hit: Any, *, allow_defer: bool = True,
            event_at: float | None = None,
            channel: str | None = None,
            visual_candidates: list[dict] | None = None) -> bool:
        if not isinstance(hit, dict):
            return False
        target_id = hit.get("id")
        if target_id is None:
            return False
        occurred_at = time.monotonic() if event_at is None else float(event_at)
        combat = self._combat_direction(
            host, hit, target_id, occurred_at,
            allow_visual=not allow_defer, channel=channel,
            visual_candidates=visual_candidates)
        if combat is None:
            if allow_defer:
                self._defer_hit("asteroid", hit, occurred_at, channel)
            return False
        direction, attacker_id, weapon, attribution = combat
        amount = self._damage(hit)
        server_damage_type = self._damage_type_label(hit)
        damage_type = self._report_damage_type(
            server_damage_type, attribution)
        if isinstance(attribution, dict):
            if attribution.get("effect_kind") == "burn":
                weapon = f"{weapon} (Burn)"
            elif attribution.get("effect_kind") == "possible_burn":
                weapon = f"{weapon} (Possible Burn)"
            elif direction == "dealt":
                self._remember_effect_source(
                    target_id, attacker_id, attribution, amount,
                    server_damage_type, occurred_at, channel)
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
            return True
        if amount is None and remaining is None:
            return True
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
            damage_type=damage_type, weapon=weapon,
            event_at=occurred_at,
            server_damage_type=server_damage_type)
        return True

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
        self._observe_energy(host)
        self._resolve_pending_hits(host)

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

    @staticmethod
    def _format_metric(value: float) -> str:
        amount = max(0.0, float(value))
        for threshold, suffix in (
                (1_000_000_000.0, "B"),
                (1_000_000.0, "M"),
                (1_000.0, "K")):
            if amount >= threshold:
                scaled = amount / threshold
                return f"{scaled:.1f}{suffix}" if scaled < 100.0 else f"{scaled:.0f}{suffix}"
        return f"{amount:.1f}" if 0.0 < amount < 100.0 else f"{amount:.0f}"

    @staticmethod
    def _format_duration(seconds: float) -> str:
        whole = max(0, int(seconds))
        hours, remainder = divmod(whole, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _draw_stats_panel(
            self, panel: Any, pygame: Any, width: int, height: int,
            body_font: Any, value_font: Any, small_font: Any,
            stats: dict) -> None:
        status = str(stats["status"]).upper()
        status_colour = {
            "RUNNING": (104, 222, 137),
            "ACTIVE": (104, 222, 137),
            "COMPLETE": (232, 190, 85),
            "IDLE": (127, 141, 154),
        }.get(status, (127, 141, 154))
        self._blit_text(
            panel, small_font, "SESSION", (140, 154, 168), 10, 73)
        self._blit_text(panel, small_font, status, status_colour, 75, 73)
        self._blit_text(
            panel, small_font, self._format_duration(stats["duration"]),
            (218, 226, 233), width - 48, 73)

        card_width = (width - 30) // 2
        for card_x, label, key, colour in (
                (10, "DEALT", "dealt", (255, 104, 88)),
                (20 + card_width, "RECEIVED", "received", (192, 214, 232))):
            values = stats[key]
            pygame.draw.rect(panel, (14, 21, 29), (card_x, 91, card_width, 84))
            pygame.draw.rect(panel, (42, 55, 68), (card_x, 91, card_width, 84), 1)
            self._blit_text(panel, small_font, label, colour, card_x + 7, 96)
            metric = "DPS" if key == "dealt" else "DTPS"
            self._blit_text(
                panel, value_font,
                f"{metric} {self._format_metric(values['dps'])}",
                (238, 241, 244), card_x + 7, 111)
            self._blit_text(
                panel, small_font,
                f"TOTAL {self._format_metric(values['total'])}",
                (166, 180, 193), card_x + 7, 135)
            self._blit_text(
                panel, small_font,
                f"AVG {self._format_metric(values['average'])}  "
                f"MAX {self._format_metric(values['maximum'])}",
                (166, 180, 193), card_x + 7, 149)
            self._blit_text(
                panel, small_font,
                f"H/S {values['hits_per_second']:.1f}  "
                f"BLOCK {values['blocked']}",
                (126, 141, 155), card_x + 7, 162)

        content_bottom = height - 48
        column_x = (10, 20 + card_width)
        heading_y = 184
        if heading_y <= content_bottom:
            self._blit_text(
                panel, small_font, "DEALT BY TYPE", (255, 104, 88),
                column_x[0], heading_y)
            self._blit_text(
                panel, small_font, "RECEIVED BY TYPE", (192, 214, 232),
                column_x[1], heading_y)
        row_y = heading_y + 17
        type_count = max(len(stats["dealt"]["types"]),
                         len(stats["received"]["types"]))
        for index in range(type_count):
            if row_y > content_bottom:
                break
            for col, key in enumerate(("dealt", "received")):
                rows = stats[key]["types"]
                if index >= len(rows):
                    continue
                row = rows[index]
                label = str(row["label"])
                max_label = max(4, (card_width - 74) // 6)
                if len(label) > max_label:
                    label = label[:max(1, max_label - 3)] + "..."
                line = (
                    f"{label} {row['percent']:.0f}% "
                    f"{self._format_metric(row['amount'])}"
                )
                colour = _DAMAGE_TYPE_COLOURS.get(
                    str(row["label"]), _DAMAGE_TYPE_COLOURS["Unknown"])
                self._blit_text(
                    panel, small_font, line, colour, column_x[col], row_y)
            row_y += 17

        if row_y + 34 <= content_bottom:
            row_y += 4
            self._blit_text(
                panel, small_font, "TOP TARGETS", (140, 154, 168),
                column_x[0], row_y)
            self._blit_text(
                panel, small_font, "TOP ATTACKERS", (140, 154, 168),
                column_x[1], row_y)
            row_y += 17
            target_count = max(len(stats["dealt"]["targets"]),
                               len(stats["received"]["targets"]))
            for index in range(target_count):
                if row_y > content_bottom:
                    break
                for col, key in enumerate(("dealt", "received")):
                    rows = stats[key]["targets"]
                    if index >= len(rows):
                        continue
                    row = rows[index]
                    amount_text = self._format_metric(row["amount"])
                    max_label = max(4, (card_width - 48) // 6)
                    label = str(row["label"])
                    if len(label) > max_label:
                        label = label[:max(1, max_label - 3)] + "..."
                    self._blit_text(
                        panel, small_font, f"{label} {amount_text}",
                        (158, 170, 182), column_x[col], row_y)
                row_y += 17

        self._blit_text(
            panel, small_font,
            f"DPE {stats['dpe']:.2f} dmg/energy   "
            f"ENERGY USED {self._format_metric(stats['energy_used'])}",
            (138, 157, 174), 10, height - 39)

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
        tab_width = min(88, max(58, (width - 16) // 4))
        self.tab_rects = {
            key: (x + 8 + index * tab_width, y + 38, tab_width - 4, 24)
            for index, key in enumerate(("all", "dealt", "received", "stats"))
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
                           ("received", "RECEIVED"), ("stats", "STATS")):
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

        stats = self.combat_stats_snapshot()
        if self.active_tab == "stats":
            self.feed_row_capacity = 0
            self.feed_rect = None
            self.scroll_max = 0
            self.scroll_track_rect = None
            self.scroll_thumb_rect = None
            self.scroll_dragging = False
            self._draw_stats_panel(
                panel, pygame, width, height, body_font, value_font,
                small_font, stats)
        else:
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
                pygame.draw.rect(panel, fill, (card_x, 70, card_width, 77))
                pygame.draw.rect(panel, border, (card_x, 70, card_width, 77), 1)
                self._blit_text(panel, small_font, label, colour, card_x + 8, 76)
                self._blit_text(
                    panel, value_font, f"{total:.0f}", (238, 241, 244),
                    card_x + 8, 91)
                self._blit_text(
                    panel, small_font, f"{hits} hits", (135, 149, 163),
                    card_x + card_width - 52, 109)
                metric = "DPS" if key == "dealt" else "DTPS"
                direction = stats[key]
                self._blit_text(
                    panel, small_font,
                    f"{metric} {self._format_metric(direction['dps'])}  "
                    f"AVG {self._format_metric(direction['average'])}",
                    (154, 168, 181), card_x + 8, 129)

            heading = {
                "all": "ALL DAMAGE HISTORY",
                "dealt": "DAMAGE DEALT HISTORY",
                "received": "DAMAGE RECEIVED HISTORY",
            }[self.active_tab]
            weapon_x = min(177, max(148, width // 3))
            target_x = max(
                weapon_x + 74,
                min(weapon_x + 128, width - 78))
            self._blit_text(
                panel, small_font, heading, (140, 154, 168), 10, 156)
            column_colour = (116, 132, 148)
            self._blit_text(panel, small_font, "DIR", column_colour, 10, 171)
            self._blit_text(
                panel, small_font, "DAMAGE", column_colour, 47, 171)
            self._blit_text(
                panel, small_font, "TYPE", column_colour, 106, 171)
            self._blit_text(
                panel, small_font, "WEAPON", column_colour, weapon_x, 171)
            self._blit_text(
                panel, small_font, "TARGET", column_colour, target_x, 171)
            pygame.draw.line(
                panel, (35, 47, 59), (10, 187), (width - 10, 187), 1)
            row_start = 193
            feed_height = max(0, height - 25 - row_start)
            row_limit = max(0, feed_height // 21 + 1)
            self.feed_row_capacity = row_limit
            self.feed_rect = (
                x + 8, y + row_start, max(0, width - 16), feed_height)
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
                amount = (
                    "BLOCK" if entry["blocked"] else f"-{entry['amount']:.0f}")
                damage_type = str(entry.get("damage_type", "Unknown"))
                type_colour = _DAMAGE_TYPE_COLOURS.get(
                    damage_type, _DAMAGE_TYPE_COLOURS["Unknown"])
                weapon = str(entry.get("weapon", "Unknown"))
                target = str(entry["target"])
                target_right_margin = 22 if self.scroll_max > 0 else 10
                weapon_limit = max(
                    8, min(22, (target_x - weapon_x - 12) // 7))
                if len(weapon) > weapon_limit:
                    weapon = weapon[:max(5, weapon_limit - 3)] + "..."
                target_limit = max(
                    8, (width - target_x - target_right_margin) // 7)
                if len(target) > target_limit:
                    target = target[:max(5, target_limit - 3)] + "..."
                self._blit_text(panel, body_font, marker, colour, 10, row_y)
                self._blit_text(
                    panel, body_font, amount, (230, 234, 238), 47, row_y)
                self._blit_text(
                    panel, body_font, damage_type, type_colour, 106, row_y)
                self._blit_text(
                    panel, body_font, weapon, (190, 203, 216), weapon_x, row_y)
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

    def _current_protocol_channel(self) -> str | None:
        stack = getattr(self.protocol_context, "stack", None)
        return stack[-1] if stack else None

    @staticmethod
    def _diagnostic_payload_shape(
            payload: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        """Describe JSON structure without recording any packet values."""
        if not payload.strip():
            return "empty", (), ()
        try:
            data = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return "text", (), ()
        if not isinstance(data, dict):
            return type(data).__name__, (), ()
        fields = tuple(sorted(str(key) for key in data))
        nested = []
        for key in fields:
            value = data.get(key)
            if isinstance(value, dict):
                child_fields = ",".join(sorted(str(child) for child in value))
                nested.append(f"{key}.{{{child_fields}}}")
            elif isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, dict):
                    child_fields = ",".join(
                        sorted(str(child) for child in first))
                    nested.append(f"{key}[].{{{child_fields}}}")
                elif isinstance(first, (list, tuple)):
                    nested.append(f"{key}[{len(first)}]")
        return "object", fields, tuple(nested)

    @staticmethod
    def _protocol_token_is_known(app: Any, token: str) -> bool:
        index = getattr(app, "_kw_index", None)
        if isinstance(index, dict):
            return token in index
        handlers = getattr(app, "_prefix_handlers", None)
        return (isinstance(handlers, dict)
                and any(
                    str(prefix).partition(" ")[0] == token
                    for prefix in handlers))

    def _capture_protocol_line(self, app: Any, line: Any) -> None:
        """Log each relevant packet shape once for the DOT data audit."""
        text = str(line)
        token, separator, payload = text.partition(" ")
        token = token.strip().upper()
        if not token:
            return
        known = self._protocol_token_is_known(app, token)
        interesting = (
            token in _PROTOCOL_DIAGNOSTIC_TOKENS
            or (token.startswith("DUNGEON_") and not known)
            or any(keyword in token for keyword in _PROTOCOL_DIAGNOSTIC_KEYWORDS)
        )
        if not interesting:
            return
        payload_type, fields, nested = self._diagnostic_payload_shape(
            payload if separator else "")
        signature = (token, known, payload_type, fields, nested)
        with self.lock:
            if signature in self.protocol_packet_shapes:
                return
            self.protocol_packet_shapes.add(signature)
        self.api.logger.debug(
            "DAMAGE_PROTOCOL_CAPTURE token=%s known=%s payload=%s fields=%s nested=%s",
            token, known, payload_type, fields, nested)

    def _install_protocol_route_diagnostic(self, host: Any) -> None:
        """Observe inbound packet shapes without altering their handling."""
        if self.protocol_route_entry is not None:
            return
        send_fn = getattr(host, "_send_fn", None)
        app = getattr(send_fn, "__self__", None)
        original = getattr(app, "_route_game_line", None)
        if not callable(original):
            self.api.logger.debug(
                "DAMAGE_PROTOCOL_CAPTURE_UNAVAILABLE route=%s",
                type(original).__name__)
            return
        instance_dict = getattr(app, "__dict__", {})
        had_instance_route = "_route_game_line" in instance_dict
        original_instance_route = instance_dict.get("_route_game_line")
        state = self

        def route_wrapper(instance, line, arrival_time=None):
            try:
                state._capture_protocol_line(instance, line)
            except Exception:
                state.api.logger.exception(
                    "damage-number protocol capture failed")
            return original(line, arrival_time)

        wrapper = types.MethodType(route_wrapper, app)
        setattr(app, "_route_game_line", wrapper)
        self.protocol_route_entry = (
            app, original, wrapper,
            had_instance_route, original_instance_route,
        )

    def _install_protocol_context_hooks(self, host: Any) -> None:
        send_fn = getattr(host, "_send_fn", None)
        app = getattr(send_fn, "__self__", None)
        handlers = getattr(app, "_prefix_handlers", None)
        if not isinstance(handlers, dict):
            self.api.logger.debug(
                "DAMAGE_PROTOCOL_CONTEXT_UNAVAILABLE handlers=%s",
                type(handlers).__name__)
            return
        state = self
        for prefix in (
                "SPACE_HIT ", "DUNGEON_AI_HIT ",
                "DUNGEON_AI_HIT_ENTITY "):
            original = handlers.get(prefix)
            if not callable(original):
                continue

            def make_handler_wrapper(original_handler, channel_name):
                def handler_wrapper(*args, **kwargs):
                    stack = getattr(state.protocol_context, "stack", None)
                    if stack is None:
                        stack = []
                        state.protocol_context.stack = stack
                    stack.append(channel_name)
                    try:
                        return original_handler(*args, **kwargs)
                    finally:
                        stack.pop()
                return handler_wrapper

            wrapper = make_handler_wrapper(original, prefix)
            handlers[prefix] = wrapper
            self.protocol_handler_entries[prefix] = (
                handlers, original, wrapper)

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
                    state.record_ship_hit(
                        instance, hit,
                        channel=state._current_protocol_channel())
                except Exception:
                    state.api.logger.exception("damage-number ship hit failed")
            return original_hit(instance, hit)

        def asteroid_wrapper(instance, hit):
            if instance is state.host:
                try:
                    state.record_asteroid_hit(
                        instance, hit,
                        channel=state._current_protocol_channel())
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
        self._install_protocol_context_hooks(host)
        self._install_protocol_route_diagnostic(host)

        for method_name, source in (
                ("add_beam", "_beams"),
                ("add_turret_beam", "_turret_beams"),
                ("add_station_beam", "_station_beams")):
            original = getattr(host_type, method_name, None)
            if not callable(original):
                continue

            def make_beam_wrapper(original_method, beam_source):
                def beam_wrapper(instance, beam, *args, **kwargs):
                    result = original_method(instance, beam, *args, **kwargs)
                    if instance is state.host:
                        try:
                            state._remember_beam_event(beam_source, beam)
                        except Exception:
                            state.api.logger.exception(
                                "damage-number beam capture failed")
                    return result
                return beam_wrapper

            wrapper = make_beam_wrapper(original, source)
            self.original_event_methods[method_name] = original
            self.event_wrappers[method_name] = wrapper
            setattr(host_type, method_name, wrapper)

        for method_name, stream_name in (
                ("set_projectiles", "player"),
                ("set_ai_projectiles", "arena")):
            original = getattr(host_type, method_name, None)
            if not callable(original):
                continue

            def make_projectile_wrapper(original_method, projectile_stream):
                def projectile_wrapper(instance, payload, *args, **kwargs):
                    if instance is state.host:
                        try:
                            state._remember_projectile_payload(
                                payload, projectile_stream)
                        except Exception:
                            state.api.logger.exception(
                                "damage-number projectile metadata capture failed")
                    result = original_method(
                        instance, payload, *args, **kwargs)
                    if instance is state.host:
                        try:
                            state._observe_owned_projectiles(instance)
                        except Exception:
                            state.api.logger.exception(
                                "damage-number projectile capture failed")
                    return result
                return projectile_wrapper

            wrapper = make_projectile_wrapper(original, stream_name)
            self.original_event_methods[method_name] = original
            self.event_wrappers[method_name] = wrapper
            setattr(host_type, method_name, wrapper)
        self.snapshot(host)

    def uninstall(self) -> None:
        host = self.host
        if self.protocol_route_entry is not None:
            app, _original, wrapper, had_instance_route, original_instance_route = (
                self.protocol_route_entry)
            instance_dict = getattr(app, "__dict__", {})
            if instance_dict.get("_route_game_line") is wrapper:
                if had_instance_route:
                    setattr(app, "_route_game_line", original_instance_route)
                else:
                    delattr(app, "_route_game_line")
            self.protocol_route_entry = None
        for _prefix, entry in self.protocol_handler_entries.items():
            handlers, original, wrapper = entry
            if handlers.get(_prefix) is wrapper:
                handlers[_prefix] = original
        self.protocol_handler_entries.clear()
        if host is not None:
            host_type = type(host)
            if getattr(host_type, "register_hit", None) is self.hit_wrapper:
                host_type.register_hit = self.original_hit
            if getattr(host_type, "register_asteroid_hit", None) is self.asteroid_wrapper:
                host_type.register_asteroid_hit = self.original_asteroid_hit
            for method_name, wrapper in self.event_wrappers.items():
                if getattr(host_type, method_name, None) is wrapper:
                    setattr(
                        host_type, method_name,
                        self.original_event_methods[method_name])
        with self.lock:
            self.items.clear()
            self.pools.clear()
            self.feed.clear()
            self.fire_intents.clear()
            self.weapon_tracks.clear()
            self.projectile_payload_metadata.clear()
            self.consumed_weapon_tracks.clear()
            self.pending_hits.clear()
            self.beam_evidence.clear()
            self.consumed_beam_evidence.clear()
            self.effect_sources.clear()
            self.protocol_packet_shapes.clear()
        self.original_event_methods.clear()
        self.event_wrappers.clear()
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
