# Changelog

## v0.6

- Replaced the 10-second Stats reset with a running combat session.
- DPS and DTPS now use the full session total and only reset when you press
  **CLEAR**.
- The Stats tab now shows total damage for the current session.

## v0.5

- Added compatibility with Star Empire 0.4.95.
- Improved weapon attribution for your main weapons, turrets, owned drones and
  fighters, including combat that continues while the main weapon is idle.
- Added conservative handling for fire-cannon residual damage. Matching rows
  are marked `Possible Burn` first, then `Burn` once a steady pattern is seen.
  Source-free hits that cannot be identified safely remain `Unknown`.

## v0.4

- Added compatibility with Star Empire 0.4.94.

## v0.3

- Added a Stats tab with encounter and rolling 10-second DPS, incoming DPS,
  average and largest hits, hit rate, blocked hits and damage-type totals.
- Added top target and attacker totals for the current encounter.
- Added ship-wide damage-per-energy and total-energy figures using the live
  energy bank and regeneration data supplied by the client.
- Improved ownership checks for player turrets, fighters and other
  player-owned attacks while continuing to ignore unrelated nearby combat.
- Added compatibility with Star Empire 0.4.71, 0.4.73 and 0.4.91.

## v0.2

- Added a movable and resizable Damage Report, shown or hidden with F8.
- Added dealt, received, and combined tabs with separate totals and hit counts.
- Kept the complete current-session damage history, with mouse-wheel and
  draggable-scrollbar navigation.
- Limited the report to the local player's dealt and received damage so nearby
  fights do not pollute the results.
- Added attacker and target names where the game provides them.
- Added full Kinetic, Laser, Thermal, Biogenic, Mining, and Energy labels.
- Added support for Star Empire 0.4.66 and the `ui.input` permission needed by
  the interactive report window.

## v0.1

First public release.

- Floating figures for damage dealt and received.
- Ship and asteroid hit positioning.
- Zero-damage feedback when a hit is fully absorbed.
- Compatibility with Star Empire 0.4.62 and 0.4.63.
