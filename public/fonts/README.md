# Bundled fonts

Static Latin-subset TTF builds generated from the [google/fonts](https://github.com/google/fonts) repository
(Regular / Bold / Italic / Bold Italic where the family provides them). The browser loads them through
`src/fonts.css`; the Docker image copies the same files to `/app/fonts` for FFmpeg `drawtext`, so the
editor preview and the rendered MP4 use identical typography.

| Family | Licence | Source directory |
| --- | --- | --- |
| Montserrat, Open Sans, Lato, Poppins, Raleway, Nunito, Source Sans 3, Oswald | SIL OFL 1.1 | `ofl/<family>` |
| Playfair Display, Merriweather, Lora, Cormorant Garamond | SIL OFL 1.1 | `ofl/<family>` |
| Bebas Neue, Anton, Pacifico, Dancing Script, Caveat, Great Vibes | SIL OFL 1.1 | `ofl/<family>` |
| Roboto | SIL OFL 1.1 (since 2023; earlier Apache 2.0) | `ofl/roboto` |
| DejaVu Sans | Bitstream Vera / public domain | Debian `fonts-dejavu-core` |

Full licence texts: `OFL.txt` (SIL Open Font License 1.1). Variable sources were instanced with
fontTools (`wght` 400/700, `wdth` 100, `opsz` 18) and subset to Latin, Latin Extended, punctuation,
currency and common symbols. Bebas Neue, Anton, Pacifico, Great Vibes have a single weight; Oswald,
Dancing Script and Caveat have no italic — the editor disables the italic toggle for those and the
renderer falls back to the upright cut.
