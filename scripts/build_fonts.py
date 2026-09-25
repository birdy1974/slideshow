#!/usr/bin/env python3
"""Download, subset and register the bundled caption fonts.

One catalogue drives three consumers that must never drift apart:

* ``public/fonts/*.ttf``  - the files. The browser loads them through
  ``src/fonts.css`` and the Docker image copies the same files to
  ``/app/fonts`` for FFmpeg (drawtext + libass), so the editor preview and the
  rendered MP4 use identical typography.
* ``registry/fonts.json`` - family -> group, files per style, licence and the
  vertical metrics both text engines need (backend/app/text_motion.py sizes
  libass fonts from ``usWinAscent + usWinDescent``; src/textMotion.ts uses the
  same numbers to put the browser baseline where libass puts it).
* ``src/fonts.css``       - generated ``@font-face`` rules.

Usage (from the repository root)::

    python3 scripts/build_fonts.py            # download missing families, rebuild json/css
    python3 scripts/build_fonts.py --offline  # only rebuild json/css from the files present

Downloads use the GitHub contents API (``gh api`` when available, plain HTTPS
otherwise) from https://github.com/google/fonts. Every download is instanced
to a static weight when needed and subset to Latin, Latin-1, Latin Extended-A,
punctuation, currency and common symbols; all OpenType layout features are
kept, so script fonts keep their ligatures and contextual connections.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FONT_DIR = REPO / "public" / "fonts"
LICENCE_DIR = FONT_DIR / "licenses"
REGISTRY = REPO / "registry" / "fonts.json"
CSS = REPO / "src" / "fonts.css"

STYLES = ("Regular", "Bold", "Italic", "BoldItalic")

# group order is the order of the font picker
GROUPS = ["Sans", "Serif", "Display", "Handwriting", "Script", "Typewriter"]

OFL = "OFL-1.1"
APACHE = "Apache-2.0"


def fam(family: str, group: str, stem: str, styles: tuple[str, ...], source: str, licence: str = OFL,
        upstream: dict[str, str] | None = None, tags: tuple[str, ...] = ()) -> dict:
    """Catalogue entry. ``stem`` names the local files (``<stem>-<Style>.ttf``);
    ``upstream`` maps a style to the file name in google/fonts when it differs."""
    return {
        "family": family, "group": group, "source": source, "licence": licence,
        "files": {style: f"{stem}-{style}.ttf" for style in styles},
        "upstream": upstream or {}, "tags": list(tags),
    }


ALL4 = STYLES
RB = ("Regular", "Bold")
R = ("Regular",)

CATALOGUE: list[dict] = [
    # ---- sans -------------------------------------------------------------------
    fam("Montserrat", "Sans", "Montserrat", ALL4, "ofl/montserrat"),
    fam("Open Sans", "Sans", "OpenSans", ALL4, "ofl/opensans"),
    fam("Roboto", "Sans", "Roboto", ALL4, "ofl/roboto"),
    fam("Lato", "Sans", "Lato", ALL4, "ofl/lato"),
    fam("Poppins", "Sans", "Poppins", ALL4, "ofl/poppins"),
    fam("Raleway", "Sans", "Raleway", ALL4, "ofl/raleway"),
    fam("Nunito", "Sans", "Nunito", ALL4, "ofl/nunito"),
    fam("Source Sans 3", "Sans", "SourceSans3", ALL4, "ofl/sourcesans3"),
    fam("Oswald", "Sans", "Oswald", RB, "ofl/oswald"),
    # ---- serif ------------------------------------------------------------------
    fam("Playfair Display", "Serif", "PlayfairDisplay", ALL4, "ofl/playfairdisplay"),
    fam("Merriweather", "Serif", "Merriweather", ALL4, "ofl/merriweather"),
    fam("Lora", "Serif", "Lora", ALL4, "ofl/lora"),
    fam("Cormorant Garamond", "Serif", "CormorantGaramond", ALL4, "ofl/cormorantgaramond"),
    # ---- display ----------------------------------------------------------------
    fam("Bebas Neue", "Display", "BebasNeue", R, "ofl/bebasneue"),
    fam("Anton", "Display", "Anton", R, "ofl/anton"),
    # ---- handwriting ------------------------------------------------------------
    fam("Caveat", "Handwriting", "Caveat", RB, "ofl/caveat", tags=("handwriting",)),
    fam("Caveat Brush", "Handwriting", "CaveatBrush", R, "ofl/caveatbrush", tags=("handwriting", "brush")),
    fam("Kalam", "Handwriting", "Kalam", RB, "ofl/kalam", tags=("handwriting",)),
    fam("Patrick Hand", "Handwriting", "PatrickHand", R, "ofl/patrickhand", tags=("handwriting",)),
    fam("Indie Flower", "Handwriting", "IndieFlower", R, "ofl/indieflower", tags=("handwriting",)),
    fam("Shadows Into Light", "Handwriting", "ShadowsIntoLight", R, "ofl/shadowsintolight",
        upstream={"Regular": "ShadowsIntoLight.ttf"}, tags=("handwriting",)),
    fam("Amatic SC", "Handwriting", "AmaticSC", RB, "ofl/amaticsc", tags=("handwriting", "condensed")),
    fam("Permanent Marker", "Handwriting", "PermanentMarker", R, "apache/permanentmarker", APACHE,
        tags=("handwriting", "marker")),
    fam("Gloria Hallelujah", "Handwriting", "GloriaHallelujah", R, "ofl/gloriahallelujah",
        upstream={"Regular": "GloriaHallelujah.ttf"}, tags=("handwriting",)),
    fam("Architects Daughter", "Handwriting", "ArchitectsDaughter", R, "ofl/architectsdaughter",
        tags=("handwriting",)),
    fam("Gochi Hand", "Handwriting", "GochiHand", R, "ofl/gochihand", tags=("handwriting", "marker")),
    fam("Handlee", "Handwriting", "Handlee", R, "ofl/handlee", tags=("handwriting",)),
    fam("Reenie Beanie", "Handwriting", "ReenieBeanie", R, "ofl/reeniebeanie",
        upstream={"Regular": "ReenieBeanie.ttf"}, tags=("handwriting", "pen")),
    fam("Nothing You Could Do", "Handwriting", "NothingYouCouldDo", R, "ofl/nothingyoucoulddo",
        upstream={"Regular": "NothingYouCouldDo.ttf"}, tags=("handwriting", "pen")),
    fam("Homemade Apple", "Handwriting", "HomemadeApple", R, "apache/homemadeapple", APACHE,
        tags=("handwriting", "cursive")),
    # ---- script & calligraphy ---------------------------------------------------
    fam("Pacifico", "Script", "Pacifico", R, "ofl/pacifico", tags=("script",)),
    fam("Dancing Script", "Script", "DancingScript", RB, "ofl/dancingscript", tags=("script",)),
    fam("Great Vibes", "Script", "GreatVibes", R, "ofl/greatvibes", tags=("script", "calligraphy")),
    fam("Sacramento", "Script", "Sacramento", R, "ofl/sacramento", tags=("script", "monoline")),
    fam("Satisfy", "Script", "Satisfy", R, "apache/satisfy", APACHE, tags=("script",)),
    fam("Allura", "Script", "Allura", R, "ofl/allura", tags=("script", "calligraphy")),
    fam("Parisienne", "Script", "Parisienne", R, "ofl/parisienne", tags=("script", "calligraphy")),
    fam("Alex Brush", "Script", "AlexBrush", R, "ofl/alexbrush", tags=("script", "brush")),
    fam("Kaushan Script", "Script", "KaushanScript", R, "ofl/kaushanscript", tags=("script", "brush")),
    fam("Yellowtail", "Script", "Yellowtail", R, "apache/yellowtail", APACHE, tags=("script",)),
    fam("Cookie", "Script", "Cookie", R, "ofl/cookie", tags=("script",)),
    fam("Courgette", "Script", "Courgette", R, "ofl/courgette", tags=("script",)),
    fam("Tangerine", "Script", "Tangerine", RB, "ofl/tangerine", tags=("script", "calligraphy")),
    fam("Lobster", "Script", "Lobster", R, "ofl/lobster", tags=("script", "display")),
    fam("Mr Dafoe", "Script", "MrDafoe", R, "ofl/mrdafoe", tags=("script", "signature")),
    fam("Pinyon Script", "Script", "PinyonScript", R, "ofl/pinyonscript", tags=("script", "calligraphy")),
    # ---- typewriter -------------------------------------------------------------
    fam("Special Elite", "Typewriter", "SpecialElite", R, "apache/specialelite", APACHE, tags=("typewriter",)),
    fam("Courier Prime", "Typewriter", "CourierPrime", ALL4, "ofl/courierprime", tags=("typewriter", "mono")),
]

# DejaVu Sans is not bundled: it ships with the Docker image's fonts-dejavu-core
# package and is the renderer's last-resort fallback.
SYSTEM_FAMILIES = [{"family": "DejaVu Sans", "group": "Sans", "system": True, "licence": "Bitstream-Vera",
                    "files": {}, "tags": []}]

UNICODES = (
    list(range(0x20, 0x7F)) + list(range(0xA0, 0x180)) +          # Basic Latin, Latin-1, Latin Extended-A
    [0x131, 0x152, 0x153, 0x2BB, 0x2BC, 0x2C6, 0x2DA, 0x2DC, 0x1E9E] +
    list(range(0x2000, 0x2070)) +                                 # general punctuation
    [0x2074, 0x20AC, 0x20BA, 0x20BD, 0x2113, 0x2116, 0x2122, 0x2126, 0x2190, 0x2191, 0x2192, 0x2193,
     0x2212, 0x2215, 0x2219, 0x221E, 0x2248, 0x2260, 0x2264, 0x2265, 0x25CF, 0x2605, 0x2665, 0x2713,
     0xFB01, 0xFB02, 0xFEFF, 0xFFFD]
)


def _fetch(path: str) -> bytes:
    """One file from google/fonts (``ofl/kalam/Kalam-Regular.ttf``)."""
    if shutil.which("gh"):
        result = subprocess.run(
            ["gh", "api", "-H", "Accept: application/vnd.github.raw", f"repos/google/fonts/contents/{path}"],
            capture_output=True, check=False)
        if result.returncode == 0 and result.stdout:
            return result.stdout
    for url in (f"https://raw.githubusercontent.com/google/fonts/main/{path}",
                f"https://api.github.com/repos/google/fonts/contents/{path}"):
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/vnd.github.raw",
                                                           "User-Agent": "slideshow-font-build"})
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except OSError:
            continue
    raise RuntimeError(f"could not download {path}")


def _subset(data: bytes, family: str, style: str) -> bytes:
    from fontTools import subset
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(data))
    if "fvar" in font:  # variable source -> static instance
        from fontTools.varLib import instancer
        axes = {a.axisTag: a for a in font["fvar"].axes}
        location = {}
        if "wght" in axes:
            location["wght"] = 700 if "Bold" in style else 400
        for tag, axis in axes.items():
            location.setdefault(tag, axis.defaultValue)
        font = instancer.instantiateVariableFont(font, location)
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.glyph_names = False
    options.hinting = True
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=UNICODES)
    subsetter.subset(font)
    _fix_names(font, family, style)
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def _fix_names(font, family: str, style: str) -> None:
    """libass finds a font by family name: make sure it is exactly ours."""
    name = font["name"]
    current = name.getDebugName(16) or name.getDebugName(1)
    if current == family:
        return
    sub = {"Regular": "Regular", "Bold": "Bold", "Italic": "Italic", "BoldItalic": "Bold Italic"}[style]
    for record in list(name.names):
        if record.nameID in (1, 16):
            name.setName(family, record.nameID, record.platformID, record.platEncID, record.langID)
        elif record.nameID in (2, 17):
            name.setName(sub, record.nameID, record.platformID, record.platEncID, record.langID)


def _metrics(path: Path) -> dict:
    from fontTools.ttLib import TTFont
    font = TTFont(str(path), lazy=True)
    os2, hhea = font["OS/2"], font["hhea"]
    return {
        "upm": font["head"].unitsPerEm,
        "winAscent": os2.usWinAscent, "winDescent": os2.usWinDescent,
        "hheaAscent": hhea.ascent, "hheaDescent": hhea.descent,
        "typoAscent": os2.sTypoAscender, "typoDescent": os2.sTypoDescender, "typoLineGap": os2.sTypoLineGap,
        "useTypo": bool(os2.fsSelection & (1 << 7)),
        "weight": os2.usWeightClass,
        "italic": bool(os2.fsSelection & 1),
        "capHeight": getattr(os2, "sCapHeight", 0) or 0,
        "xHeight": getattr(os2, "sxHeight", 0) or 0,
    }


def download_missing(entries: list[dict]) -> None:
    LICENCE_DIR.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        family = entry["family"]
        missing = [s for s, f in entry["files"].items() if not (FONT_DIR / f).exists()]
        if missing:
            print(f"  {family}: downloading {', '.join(missing)}")
            for style in missing:
                upstream = entry["upstream"].get(style) or entry["files"][style]
                data = _fetch(f"{entry['source']}/{upstream}")
                (FONT_DIR / entry["files"][style]).write_bytes(_subset(data, family, style))
        licence_file = LICENCE_DIR / f"{entry['files']['Regular'].rsplit('-', 1)[0]}-{'OFL' if entry['licence'] == OFL else 'LICENSE'}.txt"
        if not licence_file.exists():
            name = "OFL.txt" if entry["licence"] == OFL else "LICENSE.txt"
            try:
                licence_file.write_bytes(_fetch(f"{entry['source']}/{name}"))
            except RuntimeError as exc:  # pragma: no cover - network
                print(f"  ! {family}: {exc}")


def build_registry(entries: list[dict]) -> dict:
    families = []
    for entry in entries:
        files = {s: f for s, f in entry["files"].items() if (FONT_DIR / f).exists()}
        if not files:
            print(f"  ! {entry['family']}: no files, skipped")
            continue
        families.append({
            "family": entry["family"], "group": entry["group"], "licence": entry["licence"],
            "source": entry["source"], "tags": entry["tags"],
            "files": files,
            "metrics": {s: _metrics(FONT_DIR / f) for s, f in files.items()},
        })
    families += SYSTEM_FAMILIES
    families.sort(key=lambda e: GROUPS.index(e["group"]))
    return {
        "version": 1,
        "$comment": ("Bundled caption fonts - generated by scripts/build_fonts.py, do not edit by hand. "
                     "files: style -> file in public/fonts (served at /fonts, copied to /app/fonts). "
                     "metrics: font units; libass maps the ASS font size to winAscent+winDescent, CSS to upm."),
        "groups": GROUPS,
        "families": families,
    }


def build_css(registry: dict) -> str:
    lines = ["/* Generated by scripts/build_fonts.py from registry/fonts.json - do not edit by hand.",
             "   Bundled open-licence fonts (public/fonts). FFmpeg (drawtext and libass) renders with the",
             "   same files, so the editor preview matches the MP4. */"]
    for entry in registry["families"]:
        for style, file in entry["files"].items():
            weight = 700 if "Bold" in style else 400
            italic = "italic" if "Italic" in style else "normal"
            lines.append(f"@font-face{{font-family:'{entry['family']}';src:url('/fonts/{file}') format('truetype');"
                         f"font-weight:{weight};font-style:{italic};font-display:swap}}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="do not download, only rebuild json/css")
    args = parser.parse_args()
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    if not args.offline:
        print("Downloading missing families from google/fonts ...")
        download_missing(CATALOGUE)
    registry = build_registry(CATALOGUE)
    REGISTRY.write_text(json.dumps(registry, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    CSS.write_text(build_css(registry), encoding="utf-8")
    n_files = sum(len(e["files"]) for e in registry["families"])
    print(f"registry/fonts.json: {len(registry['families'])} families, {n_files} files; src/fonts.css rebuilt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
