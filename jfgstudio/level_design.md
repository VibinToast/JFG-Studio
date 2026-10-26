# Level design for Jet Force Gemini (for agents)

Read this before laying out a level. The numbers come from measuring 274 of the game's own levels (JFG-Modding
`Docs/STOCK_LEVEL_DESIGN.md`). Aim for the game's ranges; don't copy any one level.

## First decide: outdoor or indoor

The game's levels are seldom half and half. 144 of the 274 are outdoors (roofs over less than 30% of the floor) and
93 indoors (roofs over more than 60%). They are built differently:

| | Outdoor | Indoor |
|---|---|---|
| Floor area (square units) | about 5.9 million (2.7–10.6 million) | about 2.7 million (1.3–5.7 million) |
| Footprint, longest side | about 4,200 (2,700–7,100) | about 3,400 (2,200–6,000) |
| Floor height spread | about 300 (80–770); climbing levels pass 1,000 | about 85 (25–340): mostly flat |
| Height tiers | 3 (2–5) | 2 (2–4) |
| Passages (under 400 wide) | 10% of the floor (3%–22%) | 35% (7%–67%): corridors and tunnels everywhere |
| Rooms 400–800 wide | 13% | 23% |
| Areas 800–1,600 wide | 15% | 11% |
| Open ground over 1,600 wide | common in big levels (up to 69%) | none |
| Passage width | about 250 (150–400) | about 200 (150–250) |
| Ceiling | sky | rooms 600–800 high; tunnels about 300 (150 at the tightest) |
| Water or lava | in a third of them | in a third of them |

Floors rise gently in both: neighbouring floor 50 apart differs by about 18. Climbs are slopes, ramps and low steps;
a wall is a wall.

The other 37 levels are **mixed**: an outdoor area with an indoor complex off it. Build those as two parts that meet at a
doorway or tunnel mouth.

## Outdoor levels

**What they look like:**
- **Open areas round a feature:** open ground round something to look at (a lake, a crater, a tower), with paths
  leading out to side areas.
- **Tiered basins:** a basin or valley whose paths climb round its sides on ledges, up to terraces or an amphitheatre.
- **Canyon runs:** a narrow riverbed or canyon winding between cliffs, opening into clearings.
- **Islands:** platforms at different heights over a drop, reached by the jetpack (refuel pads at each).

**How to build one:**
1. **Ground:** `jfg64.terrain`. `style="hills"` for meadows and forests, `ridges` for mountains, `terraces` for
   farmland or ruins. Use `height` 100–200 and `scale` 600–900, and a new `seed` for every level.
2. **Edges:** a `rim` of hills or cliffs (300–500 high) ends the level. The game's outdoor levels end in slopes,
   cliffs and water, not walls.
3. **Clearings:** `flat` spots for the start, each fight, the key and the exit, at different heights (spread the floor
   over about 300 in 2–5 tiers).
4. **Paths:** `paths` 250 wide (150–400) between the clearings, winding, with one loop. Canyons and riverbeds:
   `jfg64.layout` links of kind `canyon` between outdoor rooms (`roof` False), or `paths` with `path_depth`.
5. **Cover:** rocks (`jfg64.rock`), pillars, ruins and trees (static props) where squads fight. Leave the open ground
   open but never empty: something every 400–600.
6. **Water:** a lake, a river along a canyon, a pool under a waterfall (Water surface, with ground under the shallows).
7. **Sky and light:** a warm sun and a cooler ambient, and fog distances to the level's size (see the main guide).

**Check:** `level_layout(level)`. Aim for roofed under 30%, passages about 10%, open areas present, and the floor height
spread and tiers in range.

## Indoor levels

**What they look like:**
- **Hub halls:** a main hall with side rooms, alcoves and doors off it, often with short side corridors.
- **Symmetric arenas:** an oval or round room with a ring walkway round a central pit or island, and doors on its sides.
- **Tunnel networks:** long winding tunnels about 200 wide that open into small rooms ("beads on a string"), mostly
  roofed.
- **Room chains:** a run of rooms one after another along a central line, the way through a building.
- **Clusters:** many small round rooms packed together (hives, nests).

**How to build one:**
1. **Rooms:** `jfg64.layout`, with every room `roof` True, 600–800 high (`height`), mostly flat (floors within about
   100 of each other, 2–4 tiers).
2. **Passages:** a third of the floor. Links are `tunnel` or `corridor`, 200 wide (150–250), tunnels 300 high (down
   to 150 at a squeeze). Give some of them bends (`via`) and `wiggle`.
3. **Variety:** mix room sizes (one big hall 1,000+ across, several 400–800, a few small ones) and shapes (`hall` for
   built places, `cave` for natural ones, `round` for chambers).
4. **Cover:** pillars and crates in the rooms (`add_cover` makes them holes), alcoves (small rooms off a hall), ledges
   on the walls.
5. **Gates:** doors between sections (key doors, switch doors, squad-locked doors) are natural at room exits.
6. **Light:** indoor levels are lit by lights (`light`, `glow`), not the sun: bake a darker ambient, and place lights
   at doorways and in rooms.

**Check:** `level_layout(level)`. Aim for roofed over 60%, passages 7%–67% (about a third), no open ground over 1,600,
and the clearance in range.

## Rewards for exploring

Every side room, dead end, ledge and glass-sealed chamber should hold something worth the detour (the owner's rule):

- **Weapons**: a weapon pickup (object types 0xFA-0x105, placed from a stock entry with `place`) or a chest
  (`chest`, `contents`); a squadron's `drop` gives one when its last member dies.
- **Keys**: `place_key_door` puts the key in a side area and the door on the main way, so exploring opens the level.
- **Tribals** to rescue: planned. The port has to set a pack's Tribal total first (decomp port request 14); until
  then don't place them.
- **Ship parts and the game's other collectables** set the player's own progress in the main game, once per save:
  ask the owner before using them in a pack.
- **Health and ammo** after fights; **full health** or a **health upgrade** (`health` 0xE9, its own game flag) as a
  bigger prize; crates and drums to break open.

## Room-graph templates

Write the plan as a list of rooms (purpose, size, shape, floor height) and links (kind, width) before building:

| Template | Rooms and links | Suits |
|---|---|---|
| Hub and spokes | One big room, 3–5 rooms off it by short links | indoor halls, outdoor clearings |
| Beads on a string | 4–8 rooms in a winding chain of tunnels or paths | tunnel networks, canyon runs |
| Loop | Rooms in a ring, plus one shortcut across | any; less backtracking |
| Arena | One round room with a ring walkway round a pit, 2–4 doors | boss or set-piece fights |
| Tiered basin | Open ground falling to a centre, paths round its sides, ledges between tiers | outdoor climbing levels |
| Islands | 3–6 platforms at different heights, refuel pads, a safe floor or kill floor below | jetpack levels |

## Any level

Play-tested lessons (a test valley got all of these wrong):

- **Stay in the game's memory.** A level's track shares the game's 8 MB with its objects: keep it within the game's
  own sizes (median about 1,200 triangles and 51 KB; 9 in 10 under 2,900 and 121 KB; the largest 5,800 and 247 KB).
  A 329 KB test level crashed. `import_geometry` warns past 121 KB and refuses a track that, with its load-time tables (about 44 bytes per triangle), overflows the game's 651 KB track buffer. Most of the game's
  textures don't repeat, and jfg64 cuts faces using them at every repeat: prefer textures that repeat both ways
  (`game_textures` repeats "both") for big floors and ceilings.
- **Never let the player out.** Every level is closed. `jfg64.terrain` and `jfg64.layout` add an invisible
  collider wall at the edge and over open walls (`boundary`; keep it on). A level meant to have drops (cliffs,
  islands) needs a kill floor below them.
- **A sky for every level**: `set_level` `sky`, a game level's of the same theme (`stock_levels` lists each one's).
  Without one the player sees black above. `validate` warns (`sky`).
- **Flat ground for flat things.** Refuel pads, crates, drums, chests, start points, exits and doors need level
  floor under their whole base, or one side hovers. Put them in flat clearings or rooms, not on noise ground
  (`validate`: placement "on a slope"; `find_flat` finds level spots).
- **Glass goes in a window.** The game's breakable glass is a square pane held by nothing. Model a square opening
  (100-520 across) in a wall and `place_glass` fills it. Never place it in the open.
- **Squads face the player** at the start (`place_squad` turns them to the start point; `face` overrides). In a bigger
  area, give squads a route (`place_patrol`) so they walk until they see the player.
- **Flying squads** drop in from above and cruise about 150 over the floor; `place_squad` places them so. Give them
  open air: not a low tunnel.

- **Start:** a quiet start area with no enemy in view, facing the way to go, with a landmark ahead.
- **Pacing:** fight, reward (health, ammo), quiet stretch, harder fight, gate. A key or switch that sends the player
  back through what they cleared.
- **Shape:** two or three distinct areas, a loop, and a secret or two (a pickup on a ledge, behind a crate).
- **Enemies:** a different enemy for each area, flying squads over open ground, and squads where there is cover (see
  the main guide's squads).
- **Variety:** each level of a pack has its own layout type, palette (the game's textures for its theme:
  `stock_level_textures`) and light.

## The process

1. **Study:** `stock_levels`, then `stock_level_layout(level)` and `stock_level_textures(level)` for two or three of
   the game's levels of the kind and theme.
2. **Plan:** outdoor or indoor, the template, the room list, where the fights, keys and exits go.
3. **Build** in Blender with jfg64 (`layout`, `terrain`, `rock`, `scatter_points`), paint with game textures or your
   own, bake light, export, then `import_geometry`.
4. **Compare:** `level_layout(level)` against the table above. Change what is out of range.
5. **Dress:** objects, squads (`place_squad`, `add_cover`), pickups, lights, sounds, doors.
6. **Validate**, then `play`. Ask the player to try it and say what you haven't tested.
