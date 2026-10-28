# TROPOS local fonts

The website serves these files locally. No runtime connection to Google Fonts is required.

| File | Typeface | Weight range | Use |
| --- | --- | --- | --- |
| `noto-sans-sc-ui.woff2` | Noto Sans SC (subset, CSS alias Tropos Sans SC) | 400–700 | Chinese interface and headings |
| `manrope-latin.woff2` | Manrope (Latin subset) | 400–800 | Latin text and tabular financial amounts |

Both fonts use the SIL Open Font License 1.1. The original license and copyright notices are retained in `OFL-NotoSansSC.txt` and `OFL-Manrope.txt`.

Official source files:

- [Noto Sans SC](https://github.com/google/fonts/tree/main/ofl/notosanssc)
- [Manrope](https://github.com/google/fonts/tree/main/ofl/manrope)

The Chinese subset includes GB2312 level-one characters, Chinese/full-width punctuation, Latin characters and characters used by the site's HTML, JavaScript, JSON and CSS when generated. Other characters use the system fallback declared in `../typography.css`. The source font's outlines, names and license notices are preserved. FontTools was used to retain variable weights, subset glyphs and compress to WOFF2.

The interface loads these fonts with `font-display: swap`. Fresh page text is readable immediately with system fallback while the local font file loads.
