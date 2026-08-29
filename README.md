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

Put the pipeline shapefile and all of its sidecar files in `input/shapefile/`.
The script automatically uses the only `.shp` in that directory. The cadastral
owner parcel shapefile defaults to
`input/cadastral/Montana_Cadastral/OWNERPARCEL.shp`.

```sh
.venv/bin/python calculate-landowners.py
```

The command creates:

- `output/landowners.geojson` — one WGS 84 feature per connected owner holding
- `output/landowners.csv` — the same owner/contact details with Excel-friendly
  column names and UTF-8 encoding
- `output/landowners.txt` — a human-readable reporting list

Run `.venv/bin/python calculate-landowners.py --help` for path overrides and
distance settings. By default, the pipeline must intersect a parcel exactly.
Same-owner parcels separated by no more than 0.5 meter are treated as attached,
which accommodates tiny cadastral alignment gaps without jumping ordinary roads.

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
`input/cadastral/Montana_Cadastral/OWNERPARCEL.shp.xml` before using the results.
