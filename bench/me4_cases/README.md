# ME4 practice questions

Two question sets feed `bench/me4_eval.py`:

- **Generated** (`data/me4_eval/me4_questions.json`, not in git): written by `bench/me4_corpus.py`
  from the facts of a fictional company, so every answer is known exactly. It tests retrieval,
  citations, table sums, and scale.
- **Public** (`public_questions.yaml`, here): hand-checked questions on real reports, which test
  parsing of messy layouts, charts, and scans. Each answer was read off the named page.

The public files are not in git. Put them in `data/me4_eval/documents/me4/public/` under these
names. Downloaded Nov 25, 2026.

| File | Source | Licence |
|---|---|---|
| `wb_philippines_economic_update_june_2025.pdf` | World Bank Open Knowledge Repository, [entry](https://openknowledge.worldbank.org/entities/publication/74f79f86-dc4d-4ad3-95c9-83301351e7ca), 6.7 MB | CC BY 3.0 IGO (World Bank repository default) |
| `adb_asian_development_outlook_december_2025.pdf` | [ADB](https://www.adb.org/sites/default/files/publication/1102431/ado-december-2025.pdf), 0.6 MB | CC BY 3.0 IGO, notice on page 18 |
| `jica_study_report_12369815.pdf` | [JICA report library](https://openjicareport.jica.go.jp/pdf/12369815.pdf), 20.6 MB | JICA copyright; used locally for testing only, never redistributed |
| `philippine_commission_report_1905_scan.pdf` | [Internet Archive](https://archive.org/details/reportphilippin10goog), 44.6 MB, 908 pages | marked not in copyright |

The Internet Archive lists the scan as a 1908 report, but its title page (PDF page 9) says Sixth
Annual Report of the Philippine Commission, 1905, Part 1, printed 1906. The name follows the page.

## What the first look found (pypdf, today's parser)

- The World Bank report logs font-encoding warnings (pypdf wants `fontTools` for its CFF fonts),
  but the body text reads cleanly.
- The ADB chart labels come out as glyph names (`/five.tab/period.tab/four.tab` for 5.4), so any
  number that lives only in an ADB chart is lost. The body text is fine.
- The JICA report has 179 pages and three nearly empty ones; its summary pages read cleanly.
- The 1905 scan has no text layer on 907 of its 908 pages, and its page images are JBIG2, which
  pypdf cannot even decode without the `jbig2dec` program. Step 2's parser must handle both
  (a decoder on the Pi, then OCR or a vision pass).
