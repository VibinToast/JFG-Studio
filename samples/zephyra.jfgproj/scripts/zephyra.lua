-- Zephyra's own script (written by hand; the triggers are in triggers.json).
-- Counts the time spent in the Sky Arena and says it at the finale platform.
local arena_start = nil
local said = false

jfg.on("level", function(level)
  jfg.log("Zephyra: level " .. level)
  arena_start = (level == jfg.level_id("level_arena")) and 0 or nil
  said = false
end)

jfg.on("tick", function(level, seconds)
  if arena_start == nil or said then return end
  local x, y, z = jfg.player()
  if z and z > 1000 and x > -300 and x < 300 then
    said = true
    jfg.log(string.format("Zephyra: the arena took %.0f seconds", seconds))
  end
end)
