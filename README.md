# Front line

A single-file map of the Russia–Ukraine front that updates itself twice a day. Three readings of the same war,
uncertainty drawn as uncertainty rather than a hard line, and settlement-level reports pulled straight from both
sides' Telegram channels.

Open `docs/index.html`. Nothing else is needed; the page carries its own map data, its own copy of Leaflet and its
own gazetteer, and works with no connection.

## Where the geometry comes from

| Layer | Source | Machine-readable? |
|---|---|---|
| Russian-held territory, contact line | **Kalibrated**, read from his Google My Maps KML export | yes, every run |
| Russian-side control map | **Карта СК by @creamy_caprice**, the map LostArmour's daily сводка is drawn against, also a Google My Maps | yes, every run |
| Ukrainian-held territory inside Russia | same map, its Ukrainian-control layer | yes |
| Contested / grey zone | same map's contested layer, where he draws one | yes |
| Claimed Russian control | ISW ArcGIS layer, and any claims layer on the source map | yes |
| Advance axes, markers | lines and pins on the source map | yes |
| Fortifications | ISW ArcGIS layer | yes |
| Settlement reports | Telegram: Russian, Ukrainian and mapper channels, read at `t.me/s/<channel>` | yes |
| Russian daily situation report | **LostArmour сводка**, parsed by axis and by control-change phrasing | yes |
| Place names, villages | GeoNames dumps for Ukraine and Russia, with Cyrillic alternates | yes |

Nothing is traced by hand and nothing is drawn from a screenshot. If a source is unreachable the page still builds,
says so on its face, and keeps the previous good data.

## The three readings

- **Map** — the base control geometry as the source map draws it, with a soft edge along the contact line and
  contested areas shaded rather than assigned to a side.
- **Claimed** — territory claimed as Russian-held, and the gap between that claim and the mapped line.
- **Russian reporting** — the Russian-side control map drawn in full, with the ground it claims beyond the base map
  shown soft-edged and measured in km², plus what Russian channels and the LostArmour сводка reported in the last two
  weeks. Ukrainian-source pins are hidden in this view by design.

Both the base reading and the Russian reading come from the same class of source (a mapper's Google My Maps) and go
through the same parser, so neither side is a second-class citizen in the pipeline.

## Uncertainty, on purpose

Three separate things feed it, and they are kept separate in the code:

1. a contested layer on the source map, if it has one;
2. disagreement between two sources covering the same ground;
3. clusters of recent reports of fighting, infiltration or advances, sized by how many reports and whether both
   sides mention the place.

The feathered band along the contact line is a rendering of that, not a claim. Nothing in it asserts control.

## Setup, about five minutes, once

1. Create a GitHub repository and upload this folder, keeping the layout.
2. **Settings → Actions → General → Workflow permissions:** *Read and write permissions*.
3. **Settings → Pages:** deploy from branch `main`, folder `/docs`. Your map is then at
   `https://<user>.github.io/<repo>/`. On the phone, open that and use Share → Add to Home Screen.
4. **Actions → Update front-line map → Run workflow.** Read the log: every source prints `ok` with a count or
   `failed` with the reason.

From then on it runs at 05:10 and 17:10 UTC and commits a new `docs/index.html`.

## Adding another mapper

Any mapper who publishes as a Google My Maps can be added without code:

```python
# config.py
EXTRA_MYMAPS = {"hudson": "<the mid from their map URL>"}
```

Layers are classified by name first (`Russian Control`, `Contested`, `Ukrainian Control`, …), then by fill colour
if the name says nothing. The Sources tab shows exactly how every layer was read, so a misclassification is visible
rather than silent. Mappers who publish images cannot be ingested; their Telegram text still is.

## Source balance

The Sources tab counts, per side, how many settlement mentions were read and how many were located, and names the
geometry each reading rests on. If the Russian column is thin, that is visible on the page rather than hidden in the
code.

## Channels read

Russian: rybar, dva_majors, voenkorKotenok, RVvoenkor, wargonzo, boris_rozhin, mod_russia, plus the LostArmour
daily сводка (which is a site, not a channel, and is parsed section by section).
Ukrainian: GeneralStaffZSU, butusov_plus, mashovets_kostyantyn, zloyodessit.
Mappers: kalibrated, suriyakUA. Edit the lists in `config.py`.

Names are matched through case endings and across both orthographies, so "в районе Мирного", "бої у Костянтинівці"
and "Красноармейск" all resolve. Anything that does not resolve is listed on the page as unmatched rather than
dropped.

## Files

- `build.py` — reads `live/`, `cache/`, `basemap/`, `gazetteer/`; writes `out/data.json` and `out/index.html`. No network.
- `sources/fetch.py` — every fetcher. Run one with `python -m sources.fetch kalibrated`.
- `sources/kml.py` — Google My Maps KML/KMZ parser.
- `gazetteer.py` — de-declension, RU↔UA spelling bridge, front-proximity disambiguation.
- `template.html` — the page.
- `cache/snapshots/` — one control snapshot per run. This is the history the slider is built from.

## Known limits

- **History starts when you first run the pipeline.** There is no back catalogue for this source, so the slider is
  thin for the first week or so.
- **The KML fetch has never been executed.** Both My Maps sources were written against Google's documented export
  URLs and tested against fixtures of that exact shape, but the machine this was built on cannot reach Google. The
  first Actions run is the real test; if Google refuses, the log says so and the fallback is ISW.
- **The LostArmour parser was tested against a real report's text**, saved as a fixture, not against a live fetch.
- Image-only mappers (Suriyak, Lajos, AMK) contribute text, not geometry.
- Telegram's public preview occasionally rate-limits; a failed channel is logged and retried next run.
