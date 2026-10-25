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
jfg64.bake_light(obj.data, obj.matrix_world, sun_dir, sun_colour, ambient)   # vertex light, per mesh
jfg64.budgets([o for o in bpy.context.scene.objects])      # {"triangles", "images", "far", "surfaces"}
jfg64.export("C:/path/level.glb")                          # +Y up, custom properties, vertex colours
```

Then `import_geometry` the `.glb` here. If `import jfg64` fails, the add-on isn't installed or enabled: ask the
player to install it (Edit > Preferences > Add-ons, the ▾ menu > Install from Disk..., `jfg64.zip`, tick "jfg64: Jet Force Gemini levels")
before modelling.

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
- **Mood**: `fogzone`, `light`, `glow`, and the level header's fog colour and distances; music per level.
- **Pacing**: a start area with no enemies in view, a squad guarding each gate, pickups (health, ammo) after fights,
  a key or switch that sends the player back through what they cleared.

## Checking

- `validate` names the file and the object (uid) of each problem; fix errors before `build` or `play`.
- `geometry_info` for the limits; `surface_at` to check an object isn't over water, a pit or nothing.
