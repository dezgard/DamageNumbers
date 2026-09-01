# Star Empire Damage Numbers

Damage Numbers makes combat easier to follow. It shows a floating damage value
over each ship or asteroid that is hit and keeps a report of the damage you deal
and receive.

## What it shows

- Floating damage numbers over the target that was hit.
- A report covering your combat only, without nearby fights mixed in.
- Separate **All**, **Dealt**, and **Received** tabs with totals and hit counts.
- A **Stats** tab with encounter DPS, incoming DPS, rolling 10-second rates,
  average and largest hits, hit rate, blocks, damage types, top targets, damage
  per energy and total energy used.
- Target names and full damage types when Star Empire provides them.
- Your main weapons, turrets, owned drones and fighters when their combat
  events can be identified safely.
- Conservative fire-cannon residual damage labels: `Possible Burn` first, then
  `Burn` when the game events show a steady matching pattern.
- Your complete damage history for the current game session.
- A movable and resizable window with mouse-wheel and scrollbar controls.

## Using the mod

Install and enable the `.semod` with Star Empire Mod Manager, then enter combat.
The Damage Report opens near the top-right of the screen.

Drag the title bar to move it and drag an edge or corner to resize it. Scroll
over the damage list or drag the scrollbar to view older hits. **CLEAR** resets
the current history and totals. Press **F8** to show or hide the report.

Disable or uninstall the mod through the Manager to return to the normal combat
display.

## Notes

This is a visual mod. It does not change damage, shields, weapons, targeting,
loot, or server data.

Star Empire does not attach a source or effect ID to every damage-over-time
packet. When a direct fire-cannon hit is observed, matching repeat ticks can
be labelled `Possible Burn` and then `Burn`. Ambiguous source-free hits remain
`Unknown` rather than being forced onto a weapon.

Damage per energy uses the ship's live energy use during the encounter and
accounts for energy regenerated between updates. It is a ship-wide efficiency
figure, not a per-weapon breakdown.

Tested with Star Empire 0.4.62, 0.4.63, 0.4.66, 0.4.71, 0.4.73, 0.4.91,
0.4.94 and 0.4.95
using Mod Loader API 1.
