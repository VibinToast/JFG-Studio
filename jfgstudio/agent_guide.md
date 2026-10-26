# Making Jet Force Gemini levels with JFG-Studio (for agents)

You edit a JFG-Studio project (`<name>.jfgproj`): a modpack for Jet Force Gemini: Recompiled. Studio checks and builds
it; the player plays it. Every tool saves the project as it goes, and each level keeps an undo history (`undo`).

## Workflow

1. `open_project` (or `new_project`), then `project_summary`.
2. Geometry: model in Blender with the jfg64 add-on (below), then `import_geometry`. Check `geometry_info` against
   the limits.
3. `add_level` on the geometry, then `set_level` for fog, sky and music. For a planet: `add_level` without one,
   `set_planet` (it names an entry level, which must exist), then `set_level {"planet": ...}` on each level.
4. A start point first: type `startpoint`, fields `startPoint` 0, `player` 0. Exits into a level arrive at one of its
   start points, by number, for player 0.
5. Lay out the play: `surface_at` and `find_flat` tell you where the floor is; `place` puts objects on it.
6. Enemies with `place_squad`, doors and keys, pickups, lights; levels joined with `connect`.
7. Reshaping a level later: export it again from Blender and `reimport_geometry` it into the level (one undo step).
   Its objects stay; the result lists the ones the new shape left buried, floating, sunk or over a pit.
   `settle_objects` puts those with a floor back on it; move the rest yourself.
8. `validate` (it includes that placement check) until there are no errors, then `play` (builds, installs and starts the game in the level). You can't
   play it yourself: say what to try, and the player reports back.

## Blender (when you model geometry)

The jfg64 add-on is **required** in Blender, **version 0.5.0 or newer** (`jfg64.__version__`): it knows the game's
surfaces and limits and exports what Studio reads. If the version is older or a call below is missing, the running
Blender has an old copy loaded: ask the player to restart Blender (after updating the add-on), don't work around it.
Through a Blender MCP server, run Python in Blender and use it rather than writing your own:

```python
import bpy, jfg64
jfg64.set_surface(bpy.data.materials["river"], "water")    # solid, water, lava, kill, collider, nocollide
jfg64.bake_light(obj.data, obj.matrix_world, sun_dir, sun_colour, ambient, tint=True)  # one mesh
jfg64.bake_scene(objects, tint=True)                       # several, with the scene's Sun lamp
jfg64.budgets(objects)                                     # {"triangles", "images", "shrunk", "far", ...}
jfg64.export("C:/path/level.glb", collection="level_1")    # or objects=[...]; default: everything visible
level = jfg64.layout("caves", [                            # rooms joined by passages, as the game's levels
    {"name": "hall", "at": (0, 0), "size": (1100, 700), "shape": "hall", "roof": True},
    {"name": "cave", "at": (1700, 500), "radius": 450, "shape": "cave", "floor": 150, "roof": True}],
    [{"from": "hall", "to": "cave", "kind": "tunnel", "wiggle": 0.7, "via": [(900, 150)]}],
    floor=jfg64.game_texture_material(0x160C), wall=..., ceiling=...)
jfg64.layout_report(level)                                 # slopes, triangles, walkable share
ground = jfg64.terrain("valley", size=(2400, 2400), height=110, seed=7, style="hills",   # or ridges, terraces
                       flat=[(0, -900, 220)], paths=[[(0, -900), (150, 300)]], rim=380,
                       material=jfg64.game_texture_material(0x160C))   # a game texture (see Themes)
jfg64.terrain_report(ground)                               # {"walkable", "steepest", "triangles", ...}
spots = jfg64.scatter_points(ground, 12, min_gap=220, max_slope=25, avoid=[(0, -900, 260)])
rocks = [jfg64.rock(f"rock{i}", radius=60, seed=i, location=p) for i, p in enumerate(spots)]
```

- **Colour**: the game draws only the vertex colours (the `Col` layer the bake writes), never a material's colour.
  Bake with `tint=True` to carry each material's Base Color in; untextured geometry is otherwise grey.
- **Spaces**: Blender's own (Z up). `sun_dir` is the direction the light travels ((0, 0, -1) = straight down);
  colours are RGB 0-1. `help(jfg64)` lists everything.
- **Several levels in one .blend**: one collection per level, exported with `collection=`. It works from a scene of
  your own: the window shows that scene for the export and goes back, and only that collection's objects go out
  (never another scene's). Don't override the scene in a context yourself: that crashed Blender in a trial.
- **Don't take over the player's Blender**: work in a new scene of your own, and leave what was open as it was.
- **Images**: `budgets` lists in `shrunk` any image the game will shrink (over 2048 pixels, or sides that aren't
  powers of two): redraw those at 64 x 32, 32 x 64, 32 x 32 ...

Then `import_geometry` the `.glb` here. If `import jfg64` fails, the add-on isn't installed or enabled: ask the
player to install it (Blender 5.2 or newer: Edit > Preferences > Add-ons, the ▾ menu > Install from Disk...,
`jfg64.zip`, tick "jfg64: Jet Force Gemini levels") before modelling.

## Themes: the game's own textures

A level can wear the game's own textures without the pack carrying them: Studio names them by number and each
player's game shows them from their own ROM. That is how to make "a forest level" look like the game's forests.

1. `stock_levels` (search, world) finds the game's levels of that kind; `stock_level_textures(level)` shows what one
   is made of: its textures by the share of the level they cover, each mostly floor, wall or ceiling, with a
   picture. Look at two or three levels of the theme.
2. `game_textures(words, level, size)` searches all of them (colour words: green, brown, grey, blue ...; dark, mid,
   bright, vivid); `texture_sheet(ids)` shows a set side by side before you choose.
3. In Blender, `jfg64.game_texture_material(int(id, 16), surface)` makes a material showing one; use it like any
   other (floors from the floors you saw, walls from the walls). `budgets` counts them as `game`, not as images.
4. Never edit those images or copy their pixels into an image of your own: a changed copy is a new image, and the
   game's art can't go in a pack (`import_geometry` and `build` refuse any image that is the game's but not a level
   texture). Your own images (painted, generated) mix freely with them.

Bake light over them as usual: the game multiplies a texture by the vertex colours, so a warm sun and a cool ambient
give the level its mood.

## Lay it out as the game's levels are

**Read `design_guide` before laying out a level**: outdoor and indoor levels are built differently, and it has the
game's numbers for each, room-graph templates and the process. In short:

Agents' first levels were one flat box or one noise field. The game's levels are rooms of different shapes and sizes
joined by corridors, tunnels and paths. Its numbers (a study of 274 of its levels, JFG-Modding
`Docs/STOCK_LEVEL_DESIGN.md`):

- **Floor**: about 2,000 x 2,000 of it (1.9 to 7.9 million square units), in a footprint 2,500 to 6,000 across.
- **Passages**: about a quarter of the floor is passages under 400 wide (most about 200 wide, 150 to 300).
- **Roofs**: a level is mostly outdoors or mostly indoors. Indoors, rooms are 600 to 800 high and tunnels about
  300 high (150 at their tightest).
- **Height**: floors spread over about 200 to 550 in 2 to 5 tiers, rising in gentle slopes and low steps. Outdoor
  levels built round climbing go well past 1,000.
- **Types**: open outdoor areas with paths round a feature; indoor halls with side rooms, alcoves and doors;
  symmetric arenas with a ring walkway round a pit; tiered basins and amphitheatres with paths climbing their sides;
  winding tunnel networks opening into small rooms; canyons and riverbeds; clusters of small round rooms; islands
  reached by jetpack.

How to do it:

1. **Study first.** `stock_levels` finds the game's levels of the kind you are making, and
   `stock_level_layout(level)` shows one's floor map with its numbers. Its textures are in `stock_level_textures`.
   Look at two or three before drawing anything.
2. **Plan rooms and links** on paper: 4 to 8 rooms of different sizes and shapes, each with a purpose (the start,
   a fight, a key, a view, the exit), at different floor heights. Join them with passages, and add a loop so the
   player isn't always backtracking.
3. **Build it** with `jfg64.layout(name, rooms, links, floor=, wall=, ceiling=)`:
   - **Rooms**: `round`, `cave` or `hall` outlines that wander (`wobble`), each at its own `floor` height, with a
     `roof` for indoor ones.
   - **Links**: `tunnel` (roofed), `corridor` or `canyon`, winding through `via` bends and `wiggle`, sloping
     between the rooms' floors.
   - **Defaults**: the game's proportions. `layout_report` checks the slopes stay walkable.
   - **Outdoors**: use `jfg64.terrain` for open ground and rims, with clearings and paths.
4. **Compare**: `level_layout(level)` measures yours the same way. Aim for the game's ranges, not a copy of one
   level.
5. **Dress it**:
   - **Cover**: `jfg64.rock`, pillars and crates where squads fight (`add_cover` makes them holes).
   - **A landmark** seen from the start.
   - **Height**: ledges to climb (Ledge surfaces).
   - **Fuel**: refuel pads by climbs.
   - **Variety**: each level of a pack its own layout type, palette and light.

### What to fill it with

Describe things by what they do; the catalog's names are the player's (never write them into the project).

| What | How | Notes |
|---|---|---|
| Ammo | `place` `ammo` (0xE8 full, 0xCF extra, 0x17E clip) | `weapon` 0-15: taken only by a hero who has that weapon |
| Health | `place` `health` (0xA9) | `amount` 0 small, 1 medium, 2 full; after fights |
| Jetpack fuel | `place` `refuel` (0xB9) | its cylinder (`radius`, `bottom`, `top`) and `maxFuel`; by climbs and gaps |
| Crates, barrels | `place` `damagecrate` (0x135 crate, 0x168 explosive drum) | breakable; rests on the floor; drums explode |
| Scenery | `place` a `staticprop` type (behaviour 54: `object_types` behaviour=54) | crates, trees, torches, huts, crystals: solid |
| Light, glow, beams | `light`, `glow`, `lightbeam` | lights are limited per level (header light slots) |
| Sound | `audio` | wind, water, machines where they are |
| Flowing water, lava | `texscroll` | makes a surface's texture move |
| Chest, keys | `chest`, `place_key_door` | rewards and gates |
| Breakable glass | `place_glass(level, a, b, bottom)` (0x12B) | a square pane: fits a square window opening in a wall; never in the open |
| Drops | squadron `drop` | 20-22 health, 23 health upgrade, 1-16 a weapon's pickup |

Enemies besides ants (`place_squad` with the type's id; `object_types` names them):

- **Ground**: 0x9C, 0x99 (shielded), 0x12F (captains), 0x121 (undead), 0x143 (armoured gunners: most of the
  game's squads of them are sentries; `place_squad` takes its one smart squad), 0xEE, 0xBE, 0xBD, 0xEF, 0x122
  (beetles and bugs), 0xE1 (small, fast), 0xF5 (a gun turret: doesn't move). Set `health` and `size` on the
  squadron for tougher or bigger ones.
- **Flying**: 0x54, 0x2D, 0x29, 0x152, 0x150, 0x2B, 0x151, 0x149. Their squadron spawns its own members and
  launches them down to a spot below it (`offsetY`): `place_squad` places the squadron high and sets that spot about
  150 over the floor, under any roof. Give them open air.
- Mix them: a different enemy for each area, a flying squad over open ground, a tough squad at the end.
- Not play-tested in a pack's own level yet: flying squads, the non-ant enemies, glass, refuel pads. Ask the player
  to check them in game, and say so in your report.

Not possible yet: **lifts and moving platforms** (the game moves them from a level's animation data, which Studio
doesn't write). Use ramps, ledges and the jetpack instead.

## Units and space

- Game units, Y up, the same as Blender's glTF export with +Y up (1 Blender unit = 1 game unit).
- Every vertex within 32767 units of the origin; keep levels well inside that.
- New images: the port adds a pack's images to the game's textures (there's no small fixed number; the example
  world adds 31). Each is stored as one 16-bit image of at most 2048 pixels (64 x 32, 32 x 64 ...) with
  power-of-two sides: a bigger one is shrunk, so draw them small. Reuse images between materials to keep the pack
  small; the vertex colours give variety for free.
- Surfaces come from material names: `water` (swimmable surface; deep water drowns heroes who can't swim), `lava`
  (hurts), `kill` (a pit floor: falling onto it is death), `collider` (invisible but solid), `nocollide` (drawn,
  not solid). Anything else is solid floor and wall.
- **`nocollide` is for decoration** (signs, grass tufts). The game looks up each triangle's collision by its place in
  the level piece, so every colliding triangle must come before every non-colliding one; the importer (jfgformats
  0.3.2) orders them, within each draw pass. One case it can't fix: a non-colliding opaque surface in the same piece
  as colliding see-through ones (water, glass). `import_geometry` and `validate` report it as an error (a trial
  level whose signs came first lost most of its floors: the player fell through).
- **Lava needs solid ground under it.** It burns Vela and Lupus, but Juno passes through lava unhurt: for him it
  isn't there, so lava over nothing is a hole he falls through (play-tested). Model lava as a surface over a solid
  floor a little below it (30 under: he wades in it). `validate` warns about lava with no floor under it, and
  `surface_at` gives `underLava`. Water works the same way for everyone: the floor under it is what they walk on.
- A walkway that rises out of the ground must start a little below it: a lip of a few units stops the hero. A raised
  deck with no ramp onto it is walked under, not onto.
- Objects that walk (enemies, start points) stand a few units above the floor (`place` does this): a spawn even a
  fraction below a sloped floor falls through it.
- **Scale**, in game units:
  - The heroes' models are about 55 (Vela) to 60 (Juno) tall, Lupus about 33 tall and 40 long.
  - Doors are big: the game's most used ones are about 120-200 wide and 130-220 tall. `describe_type door` measures
    each door type's model (`modelSizes`), and `describe_type <id>` any type's (`modelSize`, at facing 0: x across,
    y up, z along the facing). Make an opening the door's width: wider leaves a gap to walk around, which breaks the
    lock.
  - Falls of more than 240 hurt, more than 400 hurt more (Vela). Juno swims once the water is deeper than about 48.
  - Measured in play (Studio's `samples/scale_course.jfgproj`, as Juno, 2026-10-25): ramps up to 45 degrees can be
    walked up, 55 can't. Everything else on the course was within reach: blocks up to 250 high, a tunnel with a
    40-high roof, a passage 20 wide, and a 300 gap. Those are lower bounds, not limits.
  - So, to keep the player out (a locked gate, the edge of the level): seal it completely, as even a 20 gap beside
    a door lets the hero through; walls must be well over 250 high and gaps well over 300 across, or end in a kill
    floor. Slopes over 45 degrees (55 tested) can't be walked up: use them for walls the player shouldn't climb.
  - **Ledges** the hero can grab (play-tested): only where you make them. In Blender, give a block's top the
    `ledge` surface (`jfg64.set_surface`); the walls under its edges need nothing, as long as their top edges sit
    exactly on the top's corners: the importer finds them. The hero catches the edge while falling past it (walking
    off, or dropping beside a jump). Plain geometry has no ledges at all. The import report counts ledge edges per
    segment and names top edges with no wall below them; `surface_at` gives a ledge top's kind as `ledge`, and
    `level_preview` draws ledge tops yellow. On the scale course (before ledges existed) Juno got onto 250-high
    blocks with a plain jump: a ledge is a way up, not a barrier, unless the climb is higher than that.
- **Facing**: `place`, `update_object` and `connect` take degrees (0 = towards -z, the way a start point at yaw 0
  faces). Types store it in their own steps: `describe_type` gives each angle field's `perTurn` (64 for angle10,
  256 for angle8, 65536 for angle16).
- **Fog** (level header): `fogNear` and `fogFar` are on the game's depth scale, 0-1023, where nearly all of the view
  is in the top few dozen values. The example world's light haze is `fogNear` 985-990, `fogFar` 1020-1022, `fog3` =
  `fogNear`. Around 700 the fog starts at the hero's feet, and none of the game's levels starts below 900. Change it
  a few units at a time. A `fogzone`'s `near` / `far` are on the same scale: for local mist go a little under the
  level's own values (a trial's zone at 150 turned the view white).

## Objects

- `object_types` lists the schemas (decoded field layouts) and, with the player's catalog, the game's own object
  types by name. `describe_type` gives fields, ranges and named values (`keyLock: "yellow key"` works as a value).
- A type that is not fully decoded (doors, squads, patrol points) starts from a stock entry of the game: the build
  copies the bytes it doesn't know from the player's ROM. The catalog picks one; `example` [list, entry] overrides.
- Game names and text you see in the catalog are the player's, from their ROM: don't write them into the project's
  own names or text.

## Gameplay patterns

- **Squad**: a squadron, its members and a region (a polygon of region points, corners in order) they keep to.
  The region must go **counter-clockwise** in (x, z) (the tools wind it so; `validate` stops a clockwise one,
  `region-winding`): the game finds nothing inside a clockwise region, so its squad stands still and never turns.
  Members walk only inside their region and only notice a hero who is inside a region too, so make the region
  cover the floor they fight on and where the player comes from. Smart squads move only to **cover**: the corners
  of holes (obstacles cut out of the region) and inward corners of the region, where the hero can't see them. In a
  rectangle over open floor they stand and fire. `place_squad` makes every block, crate or pillar inside the region
  a hole (`add_cover` does it for a squad made before; `validate` warns: `squad-cover`); members also keep out of
  holes. So give a squad's area things to hide behind, and keep obstacles off the region's edge (cut the outline
  round those). Play-tested: ants in a counter-clockwise region with two crates made holes run between cover,
  turn and chase; in the same region without holes they crouched and fired. Patrols walking their route are
  play-tested too (watch one with the port's Tools > Cheats > Enemies ignore you). `level_preview` draws patrol
  routes in blue and holes pink and crossed.
  `place_squad` makes all of it. With `door`, the squadron keeps that door locked until all its members are dead:
  the door's `doorId` matches the squadron's, and each locked door needs its own id. Members are kept off lava,
  water and pits (moved to the nearest dry floor); the result warns about any it couldn't place dry. A squadron's
  behaviour (moving, chasing, missions) is in bytes not decoded yet, copied from a stock squad: `place_squad` copies
  one of the same enemy (a kind's base) in the mode it runs in (2, the game's usual fighting mode, or 6 for a
  patrol); a squad copying another enemy's, or a rare mode (1), ran into place and crouched without firing
  (play-tested). Of those it picks a smart squad (`behaviour` 0x11: walks to the hero and turns), never a sentry
  (9: never walks, faces one way) when the enemy has another. A squad spawns at once (`trigger` 3): a stock squad's
  own trigger (1) waits for the hero in a zone placed for its stock level, so it may never appear. `validate`
  warns about such squads (`squad-stock`, `squad-trigger`) and `fix_squad` mends one.
- **Key door**: `place_key_door(level, door_type, door_pos, key_pos)` makes a door that needs a coloured key and
  the key, in a colour no other door of the pack uses (or `colour: "red key"`; `key_level` puts the key in another
  level). Put the key somewhere the player has to reach first: a ledge, past a squad.
- **Patrols**: `place_patrol(level, points, loop, squadron)` lays a route of nodes along the points and puts the
  squadron in patrol mode: a chain is walked there and back, a ring (`loop`) round and round. A squadron walks the
  route nearest to itself: a squad more than 300 from its route is moved to the route's first node with its
  members and region. A patrol squad walks the nodes in a straight line (no path-finding: keep every link clear
  and wide enough for the squad) and fights like any squad once it sees the hero. Never give a patrol point a start
  value (`startAtLoad`): it means "ride the node's lift", and a squad near one stops at its first node for good
  (`validate`: `patrol-start`).
  Squads without a patrol stay in their region.
- **Switch doors**: `place_switch_door(level, switch_pos, door="new", door_type, door_pos)` (or `door`: a door's
  uid) places the game's shootable switch: it keeps the door locked until the player shoots it, then opens it and
  sets a game flag so it stays open after a reload (`saved` false: it locks again). Put the switch where the player
  sees it, often on a wall beside the door (`switch_lift`) or across a gap from it.
- **Game flags** are the player's saved progress, shared by the whole save file: a pack sets only its own, 103-127
  (301-351 if it needs more and adds no ammo capacity upgrades). The tools hand them out; `validate` stops any
  other.
- **Exits**: `connect(from, exit_pos, to, arrive_pos)` makes the exit and the start point it leads to (numbered
  clear of the level's own start points, and of the game level's when it is an edit of one); `arrive_start_point`
  instead arrives at a start point already there. Give every level a way back. An exit's `arg1A` 1 (the default) shows the game's stats screen when it leads into another region.
- **Planets**: a level's `planet` puts it in that planet's region (at most 32 levels per planet; the port has room
  for 9 mod regions in all). The planet's `entryLevel` is where the map starts it.
- **Reskinned enemies (kinds)**: a pack's own enemy type is one of the game's with new textures and a new size; its
  model, animations and behaviour stay the game's. `kind_slots(base)` lists the base model's texture slots (sizes,
  and whether the game's image there has transparency); `make_kind(name, display_name, base, scale)` makes it;
  `paint_kind_texture(name, colour, accent, pattern)` draws its textures (solid, noise, stripes, spots, heat) at each
  slot's size, or `make_kind`'s `textures` takes PNGs of exactly those sizes. Never copy or recolour the game's own
  images into a pack. Use it as `pack:kind/<name>`: `place_squad`'s `member_type`, or `place`. Health and speed
  aren't part of a kind: they belong to the squad (`squadron` fields `health`, `maxSpeed`, `minSpeed`).
- **Mood**: `fogzone`, `light`, `glow`, and the level header's fog colour and distances (see Fog); music per level.
- **The game's own levels**: `stock_levels` lists them (`stock:level/<id>`; `search` by name, `world`); it says
  where a new game starts, the usual way back to the game and a good place for a portal.
- **Doors, exits and start points** placed by `[x, z]` go on the lowest dry floor there with room above it: inside
  a roofed room or doorway, not on its roof; on a bridge, not in the river under it. Where there are several floors
  the result has a note naming them: give `[x, y, z]` for another (an upper floor, a walkway). Other objects go on
  the highest floor (a key on its ledge). `update_object` with `[x, z]` also puts the object on the floor. `describe_type <id>`'s
  `modelSize` gives its size and, in `from` / `to`, its box around its position. In a doorway `surface_at` returns
  the top of the wall over it: pass `below` to reach the floor.

## Planets and galaxies: how the player gets in

A planet is open on the map by its `unlock`: `"start"` (from the beginning: the default on a galaxy of the pack's
own), `"stock"` (on the game's galaxy, the default: once the game's map point its route leaves from is open),
`{"entered": "<planet>"}` / `{"completed": "<planet>"}` (once that planet's entry level was entered / it was
completed by a script), or `"script"` (when a trigger or script opens it). A galaxy of the pack's own appears to L / R
on the planet map as soon as one of its planets is open. So a pack is reachable when:
- a planet of its galaxy is `"start"`: the player presses R on the map and flies there; or
- a planet of the game's galaxy has a route from a game map point (`routes`: `from` `stock:mappoint/<n>`, a stick
  `dir`, the `back` direction); or
- an exit in one of the game's levels leads in (a portal): `edit_stock_level`, then `connect` from it to the planet's
  entry level. The example world does this, and its first planet waits for `{"entered": itself}` so it shows on the
  map once the portal was used. An edit adds to the game level: `level_info` lists the game level's own start points
  and exits (`baseLevel`) and `level_preview` draws them grey; never reuse their start point numbers (`connect`
  avoids them, `validate` says when one clashes), or the game's own arrivals there, a new game's included, break.
`validate` warns about a planet nothing leads to. The planet's `disc` is its look on the map: `index` the game disc
whose textures it wears (0-10), `x`, `y` in map coordinates (x right, y down), `radius`, and optional `colour`.
`set_planet` and `set_galaxy` take the files' full formats, with every key described.
- **Pacing**: a start area with no enemies in view, a squad guarding each gate, pickups (health, ammo) after fights,
  a key or switch that sends the player back through what they cleared.

## Checking

- `validate` names the file and the object (uid) of each problem; fix errors before `build` or `play`. Besides the
  game's rules it warns about start points inside an exit's radius, key doors whose key no level holds, planets
  nothing leads to, and objects the geometry leaves buried, floating or over a pit. It can't tell whether a door
  fills its opening or a gap lets the player around it: look.
- `level_preview` draws a level from above with its objects on it: look at each level once it is laid out. With
  `colours` it shows the baked vertex colours instead, to compare the levels' moods.
- `geometry_info` for the limits; `surface_at` to check an object isn't over water, a pit or nothing.
