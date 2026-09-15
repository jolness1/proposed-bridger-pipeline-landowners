# Proposed Bridger pipeline landowners

`calculate-landowners.py` identifies Montana cadastral parcels crossed by the
proposed pipeline. For each owner it follows touching parcels recursively, so a
connected farm or ranch is included even when most of it is several parcels away
from the pipeline. Disconnected holdings owned by the same person or entity are
not included.

## Setup

Python 3.11 or newer is recommended. From the project directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

On Windows, use `.venv\Scripts\python` in place of `.venv/bin/python`.

## Run

Put each route's pipeline shapefile and all of its sidecar files in
`input/primary-route/` and `input/secondary-route/`, respectively.
The script automatically uses the only `.shp` in each directory and processes
the routes separately. The cadastral
owner parcel shapefile defaults to
`input/Montana_Cadastral/OWNERPARCEL.shp`.

```sh
.venv/bin/python calculate-landowners.py
```

The command creates:

- `output/primary-route/primary-landowners.geojson`, `.csv`, and `.txt`
- `output/secondary-route/secondary-landowners.geojson`, `.csv`, and `.txt`

GeoJSON contains one WGS 84 feature per connected owner holding. CSV contains the
same owner/contact details with Excel-friendly column names and UTF-8 encoding.
TXT provides a human-readable reporting list.

Use `--output-dir PATH` to write both routes beneath a different parent directory,
as `PATH/primary-route/` and `PATH/secondary-route/`. To process a single shapefile,
use `--pipeline PATH`; its files go directly into `--output-dir` (default: `output/`).

Run `.venv/bin/python calculate-landowners.py --help` for path overrides and
distance settings. By default, the pipeline must intersect a parcel exactly.
Same-owner parcels separated by no more than 0.5 meter are treated as attached,
which accommodates tiny cadastral alignment gaps without jumping ordinary roads.

## Shared landowners

After generating both route outputs, run:

```sh
.venv/bin/python common-landowners.py
```

This reads `output/primary-route/primary-landowners.geojson` and
`output/secondary-route/secondary-landowners.geojson`.
These files contain the same owner details as the CSVs,
plus the mapped holdings. It writes:

- `output/shared-landowners/shared-landowners.csv`
- `output/shared-landowners/shared-landowners.txt`
- `output/shared-landowners/landowners.geojson`

Each shared named owner gets one record. Matching uses the same name normalization
as the route calculation, including semicolon-separated source-name variants.
Unnamed-owner placeholders are excluded because they do not identify an owner.
The `Primary` and `Secondary` columns preserve each route's original details,
parcel counts, acreage, and pipeline mileage. The GeoJSON combines the owner's
mapped holdings from both routes, including different parcels owned by the same
owner; overlapping areas are dissolved. It does not require the routes to cross
the same parcel.

Use `--primary-dir`, `--secondary-dir`, and `--output-dir` to override directories.

## Method and cautions

Owner names are normalized only for capitalization, punctuation, spacing, and
`&` versus `AND`. Parcels are linked when those normalized names match and their
mapped boundaries touch (or fall within the adjacency tolerance). Parcels with
no owner name are retained only when directly crossed by the pipeline.

The cadastral metadata says these data are informational and are not a legal
survey; boundaries and owner attributes can be inaccurate. Confirm ownership
against deeds and county records before publication or field contact. The source
metadata also restricts using public-record person lists as distribution/mailing
lists under Montana law. Review
`input/cadastral/MontanaCadastral_CadNSDIMetadata.xml` and
`input/Montana_Cadastral/OWNERPARCEL.shp.xml` before using the results.
