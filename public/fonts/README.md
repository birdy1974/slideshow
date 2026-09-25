# Bundled fonts

There are 49 caption families. They are static Latin-subset TTF builds made
from the [google/fonts](https://github.com/google/fonts) repository (Regular /
Bold / Italic / Bold Italic where the family provides them), plus DejaVu Sans
from the system.

The browser loads them through `src/fonts.css`. The Docker image copies the
same files to `/app/fonts` for FFmpeg (drawtext and libass), so the editor
preview and the rendered MP4 use identical typography.

`scripts/build_fonts.py` is the single source. It downloads, instances and
subsets the files, and writes `registry/fonts.json` (group, files per style,
licence and the vertical metrics both text engines use) and `src/fonts.css`:

```bash
python3 scripts/build_fonts.py            # download missing families, rebuild json/css
python3 scripts/build_fonts.py --offline  # only rebuild json/css from the files present
```

| Group | Families | Licence |
| --- | --- | --- |
| Sans | Montserrat, Open Sans, Roboto, Lato, Poppins, Raleway, Nunito, Source Sans 3, Oswald | SIL OFL 1.1 (Roboto since 2023; earlier Apache 2.0) |
| Sans | DejaVu Sans (system) | Bitstream Vera / public domain (Debian `fonts-dejavu-core`) |
| Serif | Playfair Display, Merriweather, Lora, Cormorant Garamond | SIL OFL 1.1 |
| Display | Bebas Neue, Anton | SIL OFL 1.1 |
| Handwriting | Caveat, Caveat Brush, Kalam, Patrick Hand, Indie Flower, Shadows Into Light, Amatic SC, Gloria Hallelujah, Architects Daughter, Gochi Hand, Handlee, Reenie Beanie, Nothing You Could Do | SIL OFL 1.1 |
| Handwriting | Permanent Marker, Homemade Apple | Apache 2.0 |
| Script | Pacifico, Dancing Script, Great Vibes, Sacramento, Allura, Parisienne, Alex Brush, Kaushan Script, Cookie, Courgette, Tangerine, Lobster, Mr Dafoe, Pinyon Script | SIL OFL 1.1 |
| Script | Satisfy, Yellowtail | Apache 2.0 |
| Typewriter | Courier Prime | SIL OFL 1.1 |
| Typewriter | Special Elite | Apache 2.0 |

**Licences.** Each family's licence text is in `licenses/<Family>-OFL.txt` or
`licenses/<Family>-LICENSE.txt`, and `OFL.txt` is the SIL Open Font License
1.1.

**How the files were made.** Variable sources were instanced with fontTools
(`wght` 400/700, `wdth` 100, `opsz` 18). Every family is subset to Latin, Latin
Extended-A, punctuation, currency and common symbols. **All OpenType layout
features are kept**, so script fonts keep their ligatures and contextual joins.

**Missing weights.** Families without a bold or italic cut have "one weight" or
"no italic" in the font picker. The editor disables that toggle, and both
renderers draw the real upright/regular cut instead of a faux bold or slant.
`available_style()` in `backend/app/font_registry.py` and
`FONTS_WITHOUT_BOLD` / `FONTS_WITHOUT_ITALIC` in `src/fonts.ts` apply this
rule.
