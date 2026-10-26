"""A level's track texture list: what a texscroll's `texture` field numbers (MSR "Texture scroll": an index into the
track's texture list, header +0x00), with each texture's picture.

A stock track's list is read from the player's ROM; a pack track's is the one the build will make, from jfgformats'
own importer run on the geometry the way the build runs it (game_textures.prepare_glb, stock_textures), so Studio
never guesses the importer's numbering. The pictures are RGBA as the Scene decodes the .glb's images, so a surface of
the Scene is matched to its texture by its pixels.
"""

from pathlib import Path

from jfgstudio import game_textures, refs


class TrackTextures:
    """Texture lists by track, cached (a pack track's until its .glb changes: an import takes a few seconds)."""

    def __init__(self, catalog):
        self.catalog = catalog
        self.cache: dict = {}
        self._models = None

    def of(self, track: str, glb: str | Path | None = None) -> list[dict]:
        """[{"index", "width", "height", "rgba"}] of track ref `track` ("stock:track/N", or a pack track with its
        .glb `glb`), in texture-list order."""
        from jfgformats import models, png, tracks
        r = refs.parse(track, "track")
        key = (track,) if r.is_stock else (track, str(glb), Path(glb).stat().st_mtime)
        if key in self.cache:
            return self.cache[key]
        if self._models is None:
            self._models = models.ModelData(self.catalog.assets)
        md = self._models
        if r.is_stock:
            entries, new = tracks.read_track(tracks.TrackData(self.catalog.assets).track(r.number()))["textures"], {}
        else:
            ready, _ = game_textures.Library(self.catalog).prepare_glb(Path(glb))
            res = tracks.import_track(md, str(ready), track_id=self.catalog.track_count(), log=lambda _: None,
                                      stock_textures=True)
            entries, new = tracks.read_track(res["data"])["textures"], res.get("newTextures", {})
        out = []
        for k, entry in enumerate(entries):
            data = new.get(k) or md.texture_png(entry[0] | 0x8000)[0]
            w, h, rgba = png.read_png(data)
            out.append({"index": k, "width": w, "height": h, "rgba": rgba})
        self.cache[key] = out
        return out


TEXELS_PER_SECOND = 60 / 4 / 32  # a scroll speed of 1: 1/32 texel per quarter tick, 60 ticks a second


def scroll_rate(speed: int, size: int) -> float:
    """A texscroll speed (uSpeed / vSpeed) as texture repeats a second along a side of `size` texels."""
    return speed * TEXELS_PER_SECOND / max(size, 1)
