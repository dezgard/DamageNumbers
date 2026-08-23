# Star Empire Damage Numbers

Damage Numbers adds clear floating combat figures to Star Empire. Damage dealt
to another target appears as a large red number, while hits against your own
ship use a smaller pale number so the two are easy to tell apart.

## Features

- Shows damage over the ship or asteroid that was actually hit.
- Limits the report to damage dealt by you and damage received by you; nearby
  fights between other players or AI are ignored.
- Supports normal ship combat and asteroid damage.
- Keeps the display brief and limited so combat does not become cluttered.
- Leaves each number at the impact point while it rises and fades.
- Adds a movable and resizable Damage Report with separate damage dealt and
  received totals.
- Keeps the complete damage history for the current game session instead of
  discarding older entries after a fixed number of hits.
- Organizes target names and blocked hits into **ALL**, **DEALT**, and
  **RECEIVED** feed tabs.
- Shows the full damage type on each feed row: **Kinetic**, **Laser**,
  **Thermal**, **Biogenic**, **Mining**, **Energy**, or **Unknown**.
- Labels your own ship as **Player** and uses the AI or player name supplied by
  the game when that identity is available. Received rows show the attacker's
  name directly without adding `-> Player` after it.

## Using the mod

1. Install the `.semod` with Star Empire Mod Manager.
2. Enable **Star Empire Damage Numbers**.
3. Start the game and enter combat. The Damage Report opens near the top-right
   of the screen.

Drag the title bar to move the report. Drag any edge or corner to resize it;
taller windows automatically show more history rows. Use the mouse wheel over
the feed, drag the scrollbar thumb, or click the scrollbar track to review older
entries. Each tab remembers its own scroll position, and new hits do not move an
older view out from under you. **CLEAR** resets the current session history and
totals without affecting floating numbers already on screen. Close the report
with **x** and press **F8** to show or hide it at any time.

Disable or uninstall the mod through the Manager to return to the normal combat
display.

## Safety and compatibility

The mod only observes the game's existing hit notifications and draws a visual
overlay. It does not change damage, shields, weapons, targeting, loot, or any
server-side data. Events that cannot be safely linked to you are left out of
the report instead of being presented as your damage.

The game currently supplies a damage type for each hit, but does not identify
whether that hit was direct damage or a damage-over-time tick. The report does
not guess that Thermal damage is fire or label uncertain hits as DOT damage.

Tested with Star Empire 0.4.62, 0.4.63, and 0.4.66 using Mod Loader API 1.
