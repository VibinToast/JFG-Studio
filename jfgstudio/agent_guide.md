# Making Jet Force Gemini levels with JFG-Studio (for agents)

You edit a JFG-Studio project (`<name>.jfgproj`): a modpack for Jet Force Gemini: Recompiled. Studio checks and builds
it; the player plays it. Every tool saves the project as it goes, and each level keeps an undo history (`undo`).

## Workflow

1. `open_project` (or `new_project`), then `project_summary`.
2. Geometry: model in Blender with the jfg64 add-on (below), then `import_geometry`. Check `geometry_info` against
   the limits.
3. `add_level` on the geometry; `set_level` for fog, sky, music and planet.
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

The jfg64 add-on is **required** in Blender: it knows the game's surfaces and limits and exports what Studio reads.
Through a Blender MCP server, run Python in Blender and use it rather than writing your own:

```python
import bpy, jfg64
jfg64.set_surface(bpy.data.materials["river"], "water")    # solid, water, lava, kill, collider, nocollide
jfg64.bake_light(obj.data, obj.matrix_world, sun_dir, sun_colour, ambient, tint=True)  # one mesh
jfg64.bake_scene(objects, tint=True)                       # several, with the scene's Sun lamp
jfg64.budgets(objects)                                     # {"triangles", "images", "far", "surfaces"}
jfg64.export("C:/path/level.glb", collection="level_1")    # or objects=[...]; default: everything visible
```

- **Colour**: the game draws only the vertex colours (the `Col` layer the bake writes), never a material's colour.
  Bake with `tint=True` to carry each material's Base Color in; untextured geometry is otherwise grey.
- **Spaces**: Blender's own (Z up). `sun_dir` is the direction the light travels ((0, 0, -1) = straight down);
  colours are RGB 0-1. `help(jfg64)` lists everything.
- **Several levels in one .blend**: one collection per level, exported with `collection=`.
- **Don't take over the player's Blender**: work in a new scene or file of your own, and leave what was open as it
  was.

Then `import_geometry` the `.glb` here. If `import jfg64` fails, the add-on isn't installed or enabled: ask the
player to install it (Blender 5.2 or newer: Edit > Preferences > Add-ons, the ▾ menu > Install from Disk...,
`jfg64.zip`, tick "jfg64: Jet Force Gemini levels") before modelling.

## Units and space

- Game units, Y up, the same as Blender's glTF export with +Y up (1 Blender unit = 1 game unit).
- Every vertex within 32767 units of the origin; keep levels well inside that.
- About three new images fit the game's texture table per level: reuse materials.
- Surfaces come from material names: `water` (swimmable surface; deep water drowns heroes who can't swim), `lava`
  (hurts), `kill` (a pit floor: falling onto it is death), `collider` (invisible but solid), `nocollide` (drawn,
  not solid). Anything else is solid floor and wall.
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
  - Not measured yet: the jump, the highest step, the steepest walkable slope, the clearance and width the hero
    needs. They are being measured with Studio's `samples/scale_course.jfgproj`. Until then, keep ramps at 30 degrees
    or less, steps at 16 or less, passages at least 120 wide and ceilings at least 160 high, and tell the player
    these are guesses.
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
  `place_squad` makes all of it. With `door`, the squadron keeps that door locked until all its members are dead:
  the door's `doorId` matches the squadron's, and each locked door needs its own id.
- **Key door**: a door with `keyLock` 2 or more needs a key (2 = yellow key ...); place the key pickup somewhere the
  player has to reach first (a ledge, past a squad).
- **Exits**: `connect(from, exit_pos, to, arrive_pos)` makes the exit and the start point it leads to. Give every
  level a way back. An exit's `arg1A` 1 (the default) shows the game's stats screen when it leads into another region.
- **Planets**: a level's `planet` puts it in that planet's region (at most 32 levels per planet; the port has room
  for 9 mod regions in all). The planet's `entryLevel` is where the map starts it.
- **Mood**: `fogzone`, `light`, `glow`, and the level header's fog colour and distances (see Fog); music per level.
- **The game's own levels**: `stock_levels` lists them (`stock:level/<id>`); it marks where a new game starts, the
  usual way back to the game.

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
  map once the portal was used.
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
- `level_preview` draws a level from above with its objects on it: look at each level once it is laid out.
- `geometry_info` for the limits; `surface_at` to check an object isn't over water, a pit or nothing.
