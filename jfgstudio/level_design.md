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
- **Tribals** to rescue: `place_tribal(level, pos, type)` in side rooms, dead ends and behind glass. The player
  touches one to save it. The level must be on a planet: the build gives its region the Tribal count (the port's HUD
  and stats screen), apart from the game's own Tribals. 16 at most per level.
- **Ship parts and the game's other collectables** set the player's own progress in the main game, once per save:
  ask the owner before using them in a pack.
- **Health and ammo** after fights; **full health** or a **health upgrade** (`health` 0xE9: `place` gives it its own
  game flag) as a bigger prize; crates and drums to break open.
- **Face rewards to the player**: a chest opens towards the way the player walks in (`place` `facing` [x, z] of
  the doorway), not towards the room's back wall.

## Make each level its own

Three trial levels built the same way (blob rooms on flat floors, joined by winding tunnels, in a chain with a loop)
felt the same to the player, whatever their textures and enemies. So every level must have:

- **A signature**: one set piece the level is built round and remembered by, chosen before the rooms. Pick one the
  pack's other levels don't have:

  | Signature | Built with |
  |---|---|
  | A chasm crossing: a bridge over a pit, enemies on the far side | `layout` room `"pit"`, `jfg64.bridge` |
  | A tower or shaft to climb: ramps and balconies round its walls | `platform`, `ramp`, links with `from_floor` |
  | A multi-floor building: a gallery with a balcony, rooms over rooms | `building`, `platform`, `ramp`, `from_floor` |
  | A corridor maze: square rooms off right-angled corridors, a key at its heart | `building` |
  | A river or lake to follow: water in a hollow, banks, a crossing | `terrain` paths with `path_depth`, Water surface |
  | Jetpack islands over a kill floor | `platform`s, `layout` room `"pit"` or `terrain`, refuel pads |
  | A terraced valley: open ground in steps, ledges between them | `terrain` style `terraces`, Ledge tops |
  | An arena: a ring walkway round a pit, enemies in waves | `layout` room `"pit"`, squads on triggers |

- **Its own template** (below): a different one from the pack's other levels, and a different height range (one flat
  and sprawling, one tall and vertical).
- **Its own shapes**: mix room shapes (`box` for built places, `cave` for natural ones), sizes (a cramped tunnel
  then a huge hall) and floor heights (the game's outdoor levels spread over about 300).

**Be creative.** The helpers are a start, not the limit. Through the Blender MCP you can model anything with
Python (`bmesh`, modifiers, your own meshes): arches, stairs, a crashed ship, a statue, a waterfall, a spiral ramp
round a tower. Whatever you make only has to keep to the rules: the game's limits (`budgets`, `import_geometry`),
walkable slopes (45 degrees at most), floors facing up, steps of about 70 to anything the player must reach, a
closed level (boundary colliders or kill floors), and a clean `validate` and `check_placement`. Say in your report
what you made by hand, so the player knows to look at it.

## Room-graph templates

Write the plan as a list of rooms (purpose, size, shape, floor height) and links (kind, width) before building.
Each level of a pack takes a different one:

| Template | Rooms and links | Suits |
|---|---|---|
| Hub and spokes | One big room, 3–5 rooms off it by short links | indoor halls, outdoor clearings |
| Beads on a string | 4–8 rooms in a winding chain of tunnels or paths | tunnel networks, canyon runs |
| Loop | Rooms in a ring, plus one shortcut across | any; less backtracking |
| Arena | One round room with a ring walkway round a pit, 2–4 doors | boss or set-piece fights |
| Tiered basin | Open ground falling to a centre, paths round its sides, ledges between tiers | outdoor climbing levels |
| Islands | 3–6 platforms at different heights, refuel pads, a safe floor or kill floor below | jetpack levels |
| Building | Square rooms off right-angled corridors, a floor over a floor | bases, temples, ships (`building`) |
| Vertical | A tall room or shaft, balconies and ramps up its walls, exits at several heights | towers, mines |

## Worlds of several levels

A planet's levels join into a route, the way the game's do:

- **The way on**: each level ends at a door with an exit behind it (`connect` from it to the next level's start
  point), at the far end from where the player came in. The last level's exit leaves the world (the game's way
  back, or another planet).
- **Branches**: a level may end at two doors. One takes the main path on; the other leads to an alternative path,
  a side level or two with Tribals to rescue and other rewards, that comes back to the main path (an exit into a
  later level) or leaves the world. Make the side path worth it, and lock the main door behind a key, switch or
  squad so that choosing is a real choice.
- **Mark the doors**: a light or glow over each exit door, and a different feel for each way (the side path's door
  in a side room, the main one at the end of the main hall).
- Every level has a way back to where the player came in (an exit, or the route itself), and no exit leads into
  its own level (Studio refuses that).

## Any level

Play-tested lessons (a test valley got all of these wrong):

- **Stay in the game's memory.** A level's track shares the game's 8 MB with its objects: keep it within the game's
  own sizes (median about 1,200 triangles and 51 KB; 9 in 10 under 2,900 and 121 KB; the largest 5,800 and 247 KB).
  A 329 KB test level crashed. `import_geometry` warns past 121 KB and refuses a track that, with its load-time tables (about 44 bytes per triangle), overflows the game's 651 KB track buffer. Most of the game's
  textures don't repeat, and jfg64 cuts faces using them at every repeat: prefer textures that repeat both ways
  (`game_textures` repeats "both") for big floors and ceilings. The port has given the game more memory than the
  N64 had (twice the heap, a bigger track buffer, blood splats kept outside the game's own pool) and has room for
  more: its `docs/MEMORY.md` (the wiki's "Memory" page) maps it. Studio's limits follow it; until a limit is
  raised there, keep to it here.
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
- **A door closes its passage.** It stands in a doorway smaller than itself, so it overlaps the frame: a jfg64
  link's `"door"` builds one on level floor, and `door_spots` says where the door goes. In a room's mouth the player
  walked round it; as wide as its tunnel, gaps showed round it (both play-tested). `check_placement` says when a door
  doesn't close its opening.
- **Crates and drums keep off walls** and stay the game's size (crates 39, drums 24: the defaults). A crate against
  a level's outer wall clipped into it (play-tested); `check_placement` says "clipping into geometry".
- **Water lies in a hollow**, its edge meeting the bank: a sheet raised over flat ground hovers (play-tested).
- **What the player must reach is reachable**: a key on a 210-high block was out of reach (play-tested). Give
  climbs steps of about 70; `check_placement` says when a key is out of reach.
- **One cover per enemy**: with fewer obstacles than members, two ran to the same rock and overlapped.
- **Rocks for cover are big** (radius 90+, about 100 tall) and modelled: crate objects aren't cover. Radius-60
  rocks are landmarks. Keep squad regions to 4
  corners.

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
2. **Plan:** outdoor or indoor, the signature, the template (both unlike the pack's other levels), the room list,
   where the fights, keys, rewards and exits go, and for a world, the route between its levels.
3. **Build** in Blender with jfg64 (`layout`, `building`, `platform`, `ramp`, `bridge`, `terrain`, `rock`,
   `scatter_points`) and your own modelling, paint with game textures, bake light, export, then `import_geometry`.
4. **Compare:** `level_layout(level)` against the table above. Change what is out of range.
5. **Dress:** objects, squads (`place_squad`, `add_cover`), pickups, lights, sounds, doors.
6. **Validate**, then `play` (or `play` with `launch` false, and give the player the `command` it returns, word for
   word). Ask the player to try it and say what you haven't tested.
