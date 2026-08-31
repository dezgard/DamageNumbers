from __future__ import annotations

import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


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
    host._local_entity = {
        "player_id": 99,
        "energy": 100.0,
        "max_energy": 100.0,
        "effective_energy_regen": 0.0,
        "recv_time": 0.0,
    }
    host._find_my_entity = lambda: host._local_entity
    host._space_held = False
    host._autofire = SimpleNamespace(engaged=False)
    host._targeted_npc_id = None
    host._targeted_pid = None
    host._targeted_asteroid_id = None
    host._proj_interp = {}
    host._ai_proj_interp = {}
    host._beams = []
    host._beams_lock = threading.RLock()
    host._turret_beams = []
    host._turret_beams_lock = threading.RLock()
    host._station_beams = []
    host._station_beams_lock = threading.RLock()
    return host


class DamageNumberTests(unittest.TestCase):
    def setUp(self):
        module = load_module()
        self.module = module
        self.logger = SimpleNamespace(debug=Mock(), exception=Mock())
        self.state = module._DamageState(SimpleNamespace(logger=self.logger))
        self.host = host_with_shields(100.0)
        self.state.host = self.host

    def test_just_applied_snapshot_does_not_erase_pre_hit_baseline(self):
        self.state.snapshot(self.host)
        self.host._remote_entities[7]["shields"] = 85.0

        self.state.snapshot(self.host)
        self.state.record_ship_hit(
            self.host,
            {"target_id": 7, "attacker_id": 99, "shields_remaining": 85.0},
        )

        self.assertEqual("-15", self.state.items[0][2])
        self.logger.debug.assert_called_once()

    def test_regeneration_becomes_the_next_baseline(self):
        self.state.snapshot(self.host)
        self.host._remote_entities[7]["shields"] = 85.0
        self.state.record_ship_hit(
            self.host,
            {"target_id": 7, "attacker_id": 99, "shields_remaining": 85.0},
        )
        self.host._remote_entities[7]["shields"] = 100.0
        self.state.snapshot(self.host)

        self.state.record_ship_hit(
            self.host,
            {"target_id": 7, "attacker_id": 99, "shields_remaining": 90.0},
        )

        self.assertEqual("-10", self.state.items[-1][2])

    def test_damage_only_ship_hit_uses_authoritative_amount(self):
        self.state.record_ship_hit(
            self.host, {"target_id": 7, "attacker_id": 99, "damage": 12.0})

        self.assertEqual("-12", self.state.items[0][2])
        self.logger.debug.assert_called_once()

    def test_damage_field_wins_over_pool_difference(self):
        self.state.snapshot(self.host)

        self.state.record_ship_hit(
            self.host,
            {
                "target_id": 7,
                "attacker_id": 99,
                "damage": 12.0,
                "shields_remaining": 85.0,
            },
        )

        self.assertEqual("-12", self.state.items[0][2])

    def test_non_positive_damage_only_hit_is_ignored(self):
        self.state.record_ship_hit(
            self.host, {"target_id": 7, "attacker_id": 99, "damage": 0.0})

        self.assertEqual([], self.state.items)

    def test_explicit_zero_damage_does_not_use_pool_fallback(self):
        self.state.snapshot(self.host)

        self.state.record_ship_hit(
            self.host,
            {
                "target_id": 7,
                "attacker_id": 99,
                "damage": 0.0,
                "shields_remaining": 85.0,
            },
        )

        self.assertEqual([], self.state.items)

    def test_lethal_zero_uses_the_known_pool_difference(self):
        self.state.snapshot(self.host)
        self.host._remote_entities[7]["shields"] = 0.0

        self.state.record_ship_hit(
            self.host,
            {
                "target_id": 7,
                "attacker_id": 99,
                "damage": 0.0,
                "shields_remaining": 0.0,
            },
        )

        self.assertEqual("-100", self.state.items[0][2])

    def test_confirmed_lethal_hit_without_baseline_displays_zero(self):
        self.state.pools.clear()
        self.host._remote_entities[7]["shields"] = 0.0

        self.state.record_ship_hit(
            self.host,
            {
                "target_id": 7,
                "attacker_id": 99,
                "damage": 0.0,
                "shields_remaining": 0.0,
            },
        )

        self.assertEqual("0", self.state.items[0][2])

    def test_confirmed_lethal_asteroid_without_baseline_displays_zero(self):
        self.host._asteroids = {
            3: {"id": 3, "x": 5.0, "y": 6.0, "health": 0.0},
        }

        self.state.record_asteroid_hit(
            self.host,
            {
                "id": 3,
                "attacker_id": 99,
                "damage": 0.0,
                "health_remaining": 0.0,
            },
        )

        self.assertEqual("0", self.state.items[0][2])

    def test_window_tracks_outgoing_damage_and_target_name(self):
        self.host._remote_entities[7]["name"] = "Test Raider"

        self.state.record_ship_hit(
            self.host, {"target_id": 7, "attacker_id": 99, "damage": 12.0})

        snapshot = self.state.window_snapshot()
        self.assertEqual(12.0, snapshot["dealt_total"])
        self.assertEqual(1, snapshot["dealt_hits"])
        self.assertEqual(0.0, snapshot["received_total"])
        self.assertEqual("dealt", snapshot["feed"][0]["direction"])
        self.assertEqual("Test Raider", snapshot["feed"][0]["target"])

    def test_window_tracks_incoming_damage_separately(self):
        self.host._npc_entities = {
            "npc-fa5dc56b": {
                "display_name": "Pirate Raider",
                "x": 30.0,
                "y": 40.0,
                "shields": 80.0,
            },
        }

        self.state.record_ship_hit(
            self.host,
            {
                "target_id": 99,
                "entity_id": "fa5dc56b",
                "damage": 7.0,
                "damage_type": "thermal",
            },
        )

        snapshot = self.state.window_snapshot()
        self.assertEqual(7.0, snapshot["received_total"])
        self.assertEqual(1, snapshot["received_hits"])
        self.assertEqual("Pirate Raider", snapshot["feed"][0]["target"])
        self.assertEqual("Thermal", snapshot["feed"][0]["damage_type"])

    def test_combat_stats_calculate_dps_averages_peaks_and_types(self):
        timed_hits = (
            (100.0, 7, 100.0, "dealt", 99, "Kinetic"),
            (102.0, 7, 50.0, "dealt", 99, "Thermal"),
            (103.0, 99, 25.0, "received", "npc-raider", "Laser"),
        )
        for when, target, amount, direction, attacker, damage_type in timed_hits:
            with patch.object(self.module.time, "monotonic", return_value=when):
                self.state._record_window_hit(
                    self.host, target, amount, direction, "ship", attacker,
                    damage_type)

        stats = self.state.combat_stats_snapshot(now=104.0)

        self.assertEqual("active", stats["status"])
        self.assertEqual(3.0, stats["duration"])
        self.assertAlmostEqual(50.0, stats["dealt"]["dps"])
        self.assertAlmostEqual(75.0, stats["dealt"]["average"])
        self.assertEqual(100.0, stats["dealt"]["maximum"])
        self.assertAlmostEqual(2.0 / 3.0, stats["dealt"]["hits_per_second"])
        self.assertAlmostEqual(25.0 / 3.0, stats["received"]["dps"])
        self.assertEqual("Kinetic", stats["dealt"]["types"][0]["label"])
        self.assertAlmostEqual(
            100.0 / 150.0 * 100.0,
            stats["dealt"]["types"][0]["percent"],
        )
        self.assertFalse(stats["dpe_available"])
        self.assertEqual(0.0, stats["dpe"])
        self.assertEqual(0.0, stats["energy_used"])

    def test_dpe_uses_regeneration_compensated_encounter_energy(self):
        self.host._local_entity.update({
            "energy": 100.0,
            "effective_energy_regen": 10.0,
            "recv_time": 0.0,
        })
        with patch.object(self.module.time, "monotonic", return_value=100.0):
            self.state._observe_energy(self.host)
        self.host._local_entity.update({"energy": 80.0, "recv_time": 1.0})
        with patch.object(self.module.time, "monotonic", return_value=101.0):
            self.state._observe_energy(self.host)
        with patch.object(self.module.time, "monotonic", return_value=101.5):
            self.state._record_window_hit(
                self.host, 7, 300.0, "dealt", "ship", 99, "Kinetic")

        stats = self.state.combat_stats_snapshot(now=102.0)

        self.assertAlmostEqual(30.0, stats["energy_used"])
        self.assertAlmostEqual(10.0, stats["dpe"])
        self.assertTrue(stats["dpe_available"])

    def test_full_energy_does_not_invent_regeneration_spend(self):
        self.host._local_entity.update({
            "energy": 100.0,
            "effective_energy_regen": 10.0,
            "recv_time": 0.0,
        })
        with patch.object(self.module.time, "monotonic", return_value=100.0):
            self.state._observe_energy(self.host)
        self.host._local_entity["recv_time"] = 1.0
        with patch.object(self.module.time, "monotonic", return_value=101.0):
            self.state._observe_energy(self.host)
        with patch.object(self.module.time, "monotonic", return_value=101.5):
            self.state._record_window_hit(
                self.host, 7, 300.0, "dealt", "ship", 99, "Kinetic")

        stats = self.state.combat_stats_snapshot(now=102.0)

        self.assertEqual(0.0, stats["energy_used"])
        self.assertEqual(0.0, stats["dpe"])

    def test_natural_energy_regeneration_is_not_counted_as_spend(self):
        self.host._local_entity.update({
            "energy": 80.0,
            "effective_energy_regen": 10.0,
            "recv_time": 0.0,
        })
        with patch.object(self.module.time, "monotonic", return_value=100.0):
            self.state._observe_energy(self.host)
        self.host._local_entity.update({"energy": 90.0, "recv_time": 1.0})
        with patch.object(self.module.time, "monotonic", return_value=101.0):
            self.state._observe_energy(self.host)
        with patch.object(self.module.time, "monotonic", return_value=101.5):
            self.state._record_window_hit(
                self.host, 7, 300.0, "dealt", "ship", 99, "Kinetic")

        stats = self.state.combat_stats_snapshot(now=102.0)

        self.assertEqual(0.0, stats["energy_used"])

    def test_same_energy_snapshot_is_only_observed_once(self):
        self.host._local_entity.update({
            "energy": 90.0,
            "effective_energy_regen": 10.0,
            "recv_time": 0.0,
        })
        with patch.object(self.module.time, "monotonic", return_value=100.0):
            self.state._observe_energy(self.host)
        self.host._local_entity.update({"energy": 80.0, "recv_time": 1.0})
        with patch.object(self.module.time, "monotonic", return_value=101.0):
            self.state._observe_energy(self.host)
        with patch.object(self.module.time, "monotonic", return_value=101.5):
            self.state._observe_energy(self.host)
        with patch.object(self.module.time, "monotonic", return_value=101.6):
            self.state._record_window_hit(
                self.host, 7, 200.0, "dealt", "ship", 99, "Kinetic")

        stats = self.state.combat_stats_snapshot(now=102.0)

        self.assertAlmostEqual(20.0, stats["energy_used"])
        self.assertAlmostEqual(10.0, stats["dpe"])

    def test_energy_spend_after_first_hit_updates_active_encounter(self):
        self.host._local_entity.update({
            "energy": 100.0,
            "effective_energy_regen": 10.0,
            "recv_time": 0.0,
        })
        with patch.object(self.module.time, "monotonic", return_value=100.0):
            self.state._observe_energy(self.host)
        with patch.object(self.module.time, "monotonic", return_value=100.1):
            self.state._record_window_hit(
                self.host, 7, 300.0, "dealt", "ship", 99, "Kinetic")
        self.host._local_entity.update({"energy": 80.0, "recv_time": 1.0})
        with patch.object(self.module.time, "monotonic", return_value=101.0):
            self.state._observe_energy(self.host)

        stats = self.state.combat_stats_snapshot(now=102.0)

        self.assertAlmostEqual(30.0, stats["energy_used"])
        self.assertAlmostEqual(10.0, stats["dpe"])

    def test_rolling_dps_uses_only_the_last_ten_seconds(self):
        with patch.object(self.module.time, "monotonic", return_value=0.0):
            self.state._record_window_hit(
                self.host, 7, 100.0, "dealt", "ship", 99, "Kinetic")
        with patch.object(self.module.time, "monotonic", return_value=5.0):
            self.state._record_window_hit(
                self.host, 7, 50.0, "dealt", "ship", 99, "Thermal")

        stats = self.state.combat_stats_snapshot(now=12.0)

        self.assertAlmostEqual(30.0, stats["dealt"]["dps"])
        self.assertAlmostEqual(5.0, stats["dealt"]["rolling_dps"])

    def test_quiet_period_finishes_then_next_hit_starts_new_encounter(self):
        with patch.object(self.module.time, "monotonic", return_value=0.0):
            self.state._record_window_hit(
                self.host, 7, 100.0, "dealt", "ship", 99, "Kinetic")

        finished = self.state.combat_stats_snapshot(now=11.0)

        self.assertEqual("complete", finished["status"])
        self.assertEqual(100.0, finished["dealt"]["total"])
        with patch.object(self.module.time, "monotonic", return_value=12.0):
            self.state._record_window_hit(
                self.host, 7, 40.0, "dealt", "ship", 99, "Thermal")
        next_encounter = self.state.combat_stats_snapshot(now=13.0)
        session = self.state.window_totals_snapshot()

        self.assertEqual("active", next_encounter["status"])
        self.assertEqual(40.0, next_encounter["dealt"]["total"])
        self.assertEqual(140.0, session["dealt_total"])

    def test_clear_window_resets_encounter_statistics(self):
        with patch.object(self.module.time, "monotonic", return_value=10.0):
            self.state._record_window_hit(
                self.host, 7, 100.0, "dealt", "ship", 99, "Kinetic")

        self.state.clear_window()
        stats = self.state.combat_stats_snapshot(now=11.0)

        self.assertEqual("idle", stats["status"])
        self.assertEqual(0.0, stats["dealt"]["dps"])
        self.assertEqual((), stats["dealt"]["types"])
        self.assertEqual(0.0, stats["energy_used"])
        self.assertEqual(0.0, stats["dpe"])
        self.assertIsNone(self.state.energy_previous)
        self.assertEqual([], list(self.state.energy_recent_spend))

    def test_all_canonical_damage_types_use_full_names(self):
        expected = {
            "kinetic": "Kinetic",
            "laser": "Laser",
            "thermal": "Thermal",
            "biogenic": "Biogenic",
            "mining": "Mining",
            "energy": "Energy",
        }

        for wire_name, display_name in expected.items():
            with self.subTest(wire_name=wire_name):
                self.assertEqual(
                    display_name,
                    self.state._damage_type_label(
                        {"damage_type": wire_name}),
                )

    def test_missing_or_unknown_damage_type_is_labelled_unknown(self):
        self.assertEqual("Unknown", self.state._damage_type_label({}))
        self.assertEqual(
            "Unknown",
            self.state._damage_type_label({"damage_type": "future-type"}),
        )

    def test_raw_ai_target_id_resolves_prefixed_npc_name(self):
        self.host._npc_entities = {
            "npc-fa5dc56b": {
                "display_name": "Pirate Raider",
                "x": 30.0,
                "y": 40.0,
                "shields": 80.0,
            },
        }
        self.host._targeted_npc_id = "fa5dc56b"
        self.host._space_held = True
        self.state.snapshot(self.host)

        self.state.record_ship_hit(
            self.host,
            {
                "target_id": "npc-fa5dc56b",
                "entity_id": "npc-fa5dc56b",
                "damage": 12.0,
            },
        )

        snapshot = self.state.window_snapshot()
        self.assertEqual("Pirate Raider", snapshot["feed"][0]["target"])

    def test_active_fire_accepts_unattributed_hit_on_selected_target(self):
        self.host._targeted_npc_id = "npc-raider"
        self.host._space_held = True
        self.state.snapshot(self.host)

        self.state.record_ship_hit(
            self.host, {"target_id": "npc-raider", "damage": 12.0})

        snapshot = self.state.window_snapshot()
        self.assertEqual(12.0, snapshot["dealt_total"])
        self.assertEqual("dealt", snapshot["feed"][0]["direction"])

    def test_active_fire_does_not_claim_a_different_target(self):
        self.host._targeted_npc_id = "npc-raider"
        self.host._space_held = True
        self.state.snapshot(self.host)

        self.state.record_ship_hit(
            self.host, {"target_id": "npc-bystander", "damage": 12.0})

        self.assertEqual((), self.state.window_snapshot()["feed"])

    def test_local_turret_beam_counts_without_main_weapon_input(self):
        self.host._turret_beams = [({
            "owner_id": 99,
            "ox": 0.0,
            "oy": 0.0,
            "ex": 10.0,
            "ey": 20.0,
        }, 100.0)]

        with patch.object(self.module.time, "monotonic", return_value=100.1):
            self.state.record_ship_hit(
                self.host, {"target_id": 7, "damage": 12.0})

        self.assertEqual(12.0, self.state.window_snapshot()["dealt_total"])

    def test_local_projectile_counts_after_it_leaves_client_snapshot(self):
        self.host._proj_interp = {
            "round-1": {
                "owner_id": 99,
                "x": -40.0,
                "y": 20.0,
                "vx": 500.0,
                "vy": 0.0,
                "radius": 3.0,
            },
        }
        with patch.object(self.module.time, "monotonic", return_value=100.0):
            self.state.snapshot(self.host)
        self.host._proj_interp = {}

        with patch.object(self.module.time, "monotonic", return_value=100.1):
            self.state.record_ship_hit(
                self.host, {"target_id": 7, "damage": 12.0})

        self.assertEqual(12.0, self.state.window_snapshot()["dealt_total"])

    def test_player_owned_fighter_projectile_counts_as_dealt(self):
        self.host._npc_entities["fighter-alpha"] = {
            "display_name": "My Fighter",
            "owner_id": 99,
            "x": 0.0,
            "y": 0.0,
        }
        self.host._ai_proj_interp = {
            "fighter-round": {
                "owner_id": "fighter-alpha",
                "x": -40.0,
                "y": 20.0,
                "vx": 500.0,
                "vy": 0.0,
                "radius": 3.0,
            },
        }

        with patch.object(self.module.time, "monotonic", return_value=100.0):
            self.state.snapshot(self.host)
            self.state.record_ship_hit(
                self.host, {"target_id": 7, "damage": 12.0})

        self.assertEqual(12.0, self.state.window_snapshot()["dealt_total"])

    def test_other_players_projectile_near_target_is_ignored(self):
        self.host._proj_interp = {
            "other-round": {
                "owner_id": 55,
                "x": 10.0,
                "y": 20.0,
                "vx": 0.0,
                "vy": 0.0,
                "radius": 3.0,
            },
        }

        with patch.object(self.module.time, "monotonic", return_value=100.0):
            self.state.snapshot(self.host)
            self.state.record_ship_hit(
                self.host, {"target_id": 7, "damage": 12.0})

        self.assertEqual((), self.state.window_snapshot()["feed"])

    def test_local_projectile_for_a_different_target_is_ignored(self):
        self.host._proj_interp = {
            "distant-round": {
                "owner_id": 99,
                "x": 300.0,
                "y": 300.0,
                "vx": 10.0,
                "vy": 0.0,
                "radius": 3.0,
            },
        }

        with patch.object(self.module.time, "monotonic", return_value=100.0):
            self.state.snapshot(self.host)
            self.state.record_ship_hit(
                self.host, {"target_id": 7, "damage": 12.0})

        self.assertEqual((), self.state.window_snapshot()["feed"])

    def test_expired_local_projectile_evidence_is_ignored(self):
        self.host._proj_interp = {
            "old-round": {
                "owner_id": 99,
                "x": -40.0,
                "y": 20.0,
                "vx": 500.0,
                "vy": 0.0,
                "radius": 3.0,
            },
        }
        with patch.object(self.module.time, "monotonic", return_value=100.0):
            self.state.snapshot(self.host)
        self.host._proj_interp = {}

        with patch.object(self.module.time, "monotonic", return_value=101.0):
            self.state.record_ship_hit(
                self.host, {"target_id": 7, "damage": 12.0})

        self.assertEqual((), self.state.window_snapshot()["feed"])

    def test_unrelated_nearby_combat_is_ignored(self):
        self.state.record_ship_hit(
            self.host, {"target_id": 7, "entity_id": 55, "damage": 12.0})

        snapshot = self.state.window_snapshot()
        self.assertEqual([], self.state.items)
        self.assertEqual((), snapshot["feed"])
        self.assertEqual(0.0, snapshot["dealt_total"])
        self.assertEqual(0.0, snapshot["received_total"])

    def test_ambiguous_nonlocal_combat_is_ignored(self):
        self.state.record_ship_hit(
            self.host, {"target_id": 7, "damage": 12.0})

        self.assertEqual([], self.state.items)
        self.assertEqual((), self.state.window_snapshot()["feed"])

    def test_player_owned_ai_attack_counts_as_dealt(self):
        self.host._npc_entities = {
            "drone-alpha": {
                "display_name": "My Drone",
                "owner_id": 99,
                "x": 5.0,
                "y": 6.0,
            },
        }

        self.state.record_ship_hit(
            self.host,
            {"target_id": 7, "attacker_id": "drone-alpha", "damage": 12.0},
        )

        snapshot = self.state.window_snapshot()
        self.assertEqual(12.0, snapshot["dealt_total"])
        self.assertEqual("dealt", snapshot["feed"][0]["direction"])

    def test_local_target_without_attacker_is_still_received(self):
        self.state.record_ship_hit(
            self.host, {"target_id": 99, "damage": 7.0})

        snapshot = self.state.window_snapshot()
        self.assertEqual(7.0, snapshot["received_total"])
        self.assertEqual("Player", snapshot["feed"][0]["target"])

    def test_window_retains_complete_session_history(self):
        for amount in range(1, 151):
            self.state.record_ship_hit(
                self.host,
                {"target_id": 7, "attacker_id": 99, "damage": amount},
            )

        snapshot = self.state.window_snapshot()
        self.assertEqual(sum(range(1, 151)), snapshot["dealt_total"])
        self.assertEqual(150, snapshot["dealt_hits"])
        self.assertEqual(150, len(snapshot["feed"]))
        self.assertEqual(1.0, snapshot["feed"][0]["amount"])
        self.assertEqual(150.0, snapshot["feed"][-1]["amount"])

    def test_feed_view_scrolls_from_newest_to_older_rows(self):
        for amount in range(1, 7):
            self.state.record_ship_hit(
                self.host,
                {"target_id": 7, "attacker_id": 99, "damage": amount},
            )

        newest = self.state.feed_view(3)
        self.state._adjust_feed_scroll(2, 3)
        older = self.state.feed_view(3)

        self.assertEqual([4.0, 5.0, 6.0], [
            row["amount"] for row in newest["rows"]])
        self.assertEqual([2.0, 3.0, 4.0], [
            row["amount"] for row in older["rows"]])
        self.assertEqual(2, older["offset"])

    def test_new_hit_keeps_scrolled_history_anchored(self):
        for amount in range(1, 7):
            self.state.record_ship_hit(
                self.host,
                {"target_id": 7, "attacker_id": 99, "damage": amount},
            )
        self.state._adjust_feed_scroll(2, 3)

        self.state.record_ship_hit(
            self.host,
            {"target_id": 7, "attacker_id": 99, "damage": 7.0},
        )

        view = self.state.feed_view(3)
        self.assertEqual([2.0, 3.0, 4.0], [
            row["amount"] for row in view["rows"]])

    def test_clear_window_resets_feed_and_totals_only(self):
        self.state.record_ship_hit(
            self.host, {"target_id": 7, "attacker_id": 99, "damage": 12.0})
        self.assertEqual(1, len(self.state.items))

        self.state.clear_window()

        snapshot = self.state.window_snapshot()
        self.assertEqual(0.0, snapshot["dealt_total"])
        self.assertEqual(0, snapshot["dealt_hits"])
        self.assertEqual((), snapshot["feed"])
        self.assertEqual(1, len(self.state.items))
        self.assertEqual({"all": 0, "dealt": 0, "received": 0},
                         self.state.feed_scroll)

    def test_mouse_wheel_scrolls_feed_and_is_consumed(self):
        for amount in range(1, 7):
            self.state.record_ship_hit(
                self.host,
                {"target_id": 7, "attacker_id": 99, "damage": amount},
            )
        pygame = SimpleNamespace(MOUSEWHEEL=5, mouse=SimpleNamespace(
            get_pos=lambda: (30, 190)))
        self.state.pygame = pygame
        self.state.window_rect = (10, 10, 360, 270)
        self.state.feed_rect = (18, 174, 344, 81)
        self.state.feed_row_capacity = 3

        consumed = self.state.handle_event(
            self.host, SimpleNamespace(type=5, y=1, pos=(30, 190)))

        self.assertTrue(consumed)
        self.assertEqual(3, self.state.feed_scroll["all"])

    def test_scrollbar_thumb_can_be_dragged_to_oldest_rows(self):
        for amount in range(1, 11):
            self.state.record_ship_hit(
                self.host,
                {"target_id": 7, "attacker_id": 99, "damage": amount},
            )
        pygame = SimpleNamespace(
            MOUSEBUTTONDOWN=2, MOUSEBUTTONUP=3, MOUSEMOTION=4,
            MOUSEWHEEL=5, mouse=SimpleNamespace(get_pos=lambda: (0, 0)))
        self.state.pygame = pygame
        self.state.window_rect = (10, 10, 360, 270)
        self.state.feed_rect = (18, 174, 344, 81)
        self.state.feed_row_capacity = 3
        self.state.scroll_max = 7
        self.state.scroll_track_rect = (357, 174, 7, 81)
        self.state.scroll_thumb_rect = (357, 233, 7, 22)

        down = self.state.handle_event(
            self.host,
            SimpleNamespace(type=2, button=1, pos=(360, 240)))
        motion = self.state.handle_event(
            self.host, SimpleNamespace(type=4, pos=(360, 181)))
        up = self.state.handle_event(
            self.host,
            SimpleNamespace(type=3, button=1, pos=(360, 181)))

        self.assertTrue(down)
        self.assertTrue(motion)
        self.assertTrue(up)
        self.assertEqual(7, self.state.feed_scroll["all"])
        self.assertFalse(self.state.scroll_dragging)

    def test_f8_toggles_window_without_using_escape(self):
        pygame = SimpleNamespace(KEYDOWN=1, K_F8=119, K_ESCAPE=27)
        self.state.pygame = pygame
        self.state.window_open = True

        consumed = self.state.handle_event(
            self.host, SimpleNamespace(type=1, key=119))
        escape_consumed = self.state.handle_event(
            self.host, SimpleNamespace(type=1, key=27))

        self.assertTrue(consumed)
        self.assertFalse(self.state.window_open)
        self.assertFalse(escape_consumed)

    def test_tabs_filter_dealt_and_received_rows(self):
        self.state.record_ship_hit(
            self.host, {"target_id": 7, "attacker_id": 99, "damage": 12.0})
        self.state.record_ship_hit(
            self.host, {"target_id": 99, "damage": 7.0})

        self.state.active_tab = "dealt"
        dealt = self.state.filtered_feed()
        self.state.active_tab = "received"
        received = self.state.filtered_feed()
        self.state.active_tab = "all"
        combined = self.state.filtered_feed()

        self.assertEqual(["dealt"], [row["direction"] for row in dealt])
        self.assertEqual(["received"], [row["direction"] for row in received])
        self.assertEqual(2, len(combined))

    def test_clicking_tab_changes_active_feed(self):
        pygame = SimpleNamespace(
            KEYDOWN=1, K_F8=119, MOUSEBUTTONDOWN=2,
            MOUSEBUTTONUP=3, MOUSEMOTION=4, MOUSEWHEEL=5,
        )
        self.state.pygame = pygame
        self.state.window_x = 10
        self.state.window_y = 10
        self.state.window_rect = (10, 10, 360, 270)
        self.state.tab_rects = {"dealt": (20, 48, 80, 24)}

        consumed = self.state.handle_event(
            self.host,
            SimpleNamespace(type=pygame.MOUSEBUTTONDOWN, button=1, pos=(30, 55)),
        )

        self.assertTrue(consumed)
        self.assertEqual("dealt", self.state.active_tab)

    def test_bottom_right_resize_grows_and_clamps_to_screen(self):
        pygame = SimpleNamespace(
            KEYDOWN=1, K_F8=119, MOUSEBUTTONDOWN=2,
            MOUSEBUTTONUP=3, MOUSEMOTION=4, MOUSEWHEEL=5,
        )
        screen = SimpleNamespace(get_size=lambda: (800, 600))
        self.state.pygame = pygame
        self.state.window_x = 100
        self.state.window_y = 100
        self.state.window_rect = (100, 100, 360, 270)

        down = self.state.handle_event(
            self.host,
            SimpleNamespace(type=pygame.MOUSEBUTTONDOWN, button=1, pos=(459, 369)),
            screen,
        )
        motion = self.state.handle_event(
            self.host,
            SimpleNamespace(type=pygame.MOUSEMOTION, pos=(2000, 2000)),
            screen,
        )

        self.assertTrue(down)
        self.assertTrue(motion)
        self.assertEqual("bottom-right", self.state.resizing)
        self.assertEqual((700, 500), (
            self.state.window_width, self.state.window_height))

    def test_resize_never_shrinks_below_minimum(self):
        screen = SimpleNamespace(get_size=lambda: (800, 600))
        self.state.window_x = 100
        self.state.window_y = 100
        self.state.window_width = 360
        self.state.window_height = 270
        self.state.resizing = "bottom-right"
        self.state.resize_start = (459, 369, 100, 100, 360, 270)

        self.state._apply_resize((150, 150), screen)

        self.assertEqual(self.state.min_window_width, self.state.window_width)
        self.assertEqual(self.state.min_window_height, self.state.window_height)


if __name__ == "__main__":
    unittest.main()
