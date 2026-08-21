from __future__ import annotations

import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).parents[1] / "package_source" / "__init__.py"


def load_module():
    spec = importlib.util.spec_from_file_location("damage_numbers_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def host_with_shields(value: float):
    host = SimpleNamespace()
    host._lock = threading.RLock()
    host._remote_entities = {
        7: {"player_id": 7, "x": 10.0, "y": 20.0, "shields": value},
    }
    host._npc_entities = {}
    host._asteroids = {}
    host._local_player_id = 99
    host._local_x = 0.0
    host._local_y = 0.0
    host._find_my_entity = lambda: {"player_id": 99}
    return host


class DamageNumberTests(unittest.TestCase):
    def setUp(self):
        module = load_module()
        self.logger = SimpleNamespace(debug=Mock(), exception=Mock())
        self.state = module._DamageState(SimpleNamespace(logger=self.logger))
        self.host = host_with_shields(100.0)
        self.state.host = self.host

    def test_just_applied_snapshot_does_not_erase_pre_hit_baseline(self):
        self.state.snapshot(self.host)
        self.host._remote_entities[7]["shields"] = 85.0

        self.state.snapshot(self.host)
        self.state.record_ship_hit(
            self.host, {"target_id": 7, "shields_remaining": 85.0})

        self.assertEqual("-15", self.state.items[0][2])
        self.logger.debug.assert_called_once()

    def test_regeneration_becomes_the_next_baseline(self):
        self.state.snapshot(self.host)
        self.host._remote_entities[7]["shields"] = 85.0
        self.state.record_ship_hit(
            self.host, {"target_id": 7, "shields_remaining": 85.0})
        self.host._remote_entities[7]["shields"] = 100.0
        self.state.snapshot(self.host)

        self.state.record_ship_hit(
            self.host, {"target_id": 7, "shields_remaining": 90.0})

        self.assertEqual("-10", self.state.items[-1][2])

    def test_damage_only_ship_hit_uses_authoritative_amount(self):
        self.state.record_ship_hit(
            self.host, {"target_id": 7, "damage": 12.0})

        self.assertEqual("-12", self.state.items[0][2])
        self.logger.debug.assert_called_once()

    def test_damage_field_wins_over_pool_difference(self):
        self.state.snapshot(self.host)

        self.state.record_ship_hit(
            self.host,
            {"target_id": 7, "damage": 12.0, "shields_remaining": 85.0},
        )

        self.assertEqual("-12", self.state.items[0][2])

    def test_non_positive_damage_only_hit_is_ignored(self):
        self.state.record_ship_hit(
            self.host, {"target_id": 7, "damage": 0.0})

        self.assertEqual([], self.state.items)

    def test_explicit_zero_damage_does_not_use_pool_fallback(self):
        self.state.snapshot(self.host)

        self.state.record_ship_hit(
            self.host,
            {"target_id": 7, "damage": 0.0, "shields_remaining": 85.0},
        )

        self.assertEqual([], self.state.items)

    def test_lethal_zero_uses_the_known_pool_difference(self):
        self.state.snapshot(self.host)
        self.host._remote_entities[7]["shields"] = 0.0

        self.state.record_ship_hit(
            self.host,
            {"target_id": 7, "damage": 0.0, "shields_remaining": 0.0},
        )

        self.assertEqual("-100", self.state.items[0][2])

    def test_confirmed_lethal_hit_without_baseline_displays_zero(self):
        self.state.pools.clear()
        self.host._remote_entities[7]["shields"] = 0.0

        self.state.record_ship_hit(
            self.host,
            {"target_id": 7, "damage": 0.0, "shields_remaining": 0.0},
        )

        self.assertEqual("0", self.state.items[0][2])

    def test_confirmed_lethal_asteroid_without_baseline_displays_zero(self):
        self.host._asteroids = {
            3: {"id": 3, "x": 5.0, "y": 6.0, "health": 0.0},
        }

        self.state.record_asteroid_hit(
            self.host,
            {"id": 3, "damage": 0.0, "health_remaining": 0.0},
        )

        self.assertEqual("0", self.state.items[0][2])


if __name__ == "__main__":
    unittest.main()
