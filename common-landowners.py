#!/usr/bin/env python3
"""Find named landowners present in both pipeline route outputs."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely


PROJECT_ROOT = Path(__file__).resolve().parent
UNNAMED_OWNER = "Owner not listed in cadastral data"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--primary-dir",
        type=Path,
        default=PROJECT_ROOT / "output/primary-route",
        help="Primary route output directory (default: output/primary-route).",
    )
    parser.add_argument(
        "--secondary-dir",
        type=Path,
        default=PROJECT_ROOT / "output/secondary-route",
        help="Secondary route output directory (default: output/secondary-route).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "output/shared-landowners",
        help="Shared output directory (default: output/shared-landowners).",
    )
    return parser.parse_args()


def normalize_name(name: str) -> str:
    """Use the same punctuation, spacing, and ampersand rules as the calculator."""
    return re.sub(r"[^A-Z0-9]+", " ", name.upper().replace("&", " AND ")).strip()


def owner_key(value: object) -> str:
    if pd.isna(value):
        return ""
    # The calculator joins source-name variants with semicolons. Normalize each
    # variant separately so a route with fewer variants still matches.
    keys = {normalize_name(name) for name in str(value).split(";")}
    keys.discard("")
    keys.discard(normalize_name(UNNAMED_OWNER))
    if len(keys) > 1:
        raise RuntimeError(f"Owner has conflicting normalized name variants: {value}")
    return next(iter(keys), "")


def read_route(path: Path) -> gpd.GeoDataFrame:
    # GeoJSON includes all CSV attributes as well as the mapped holdings.
    if not path.is_file():
        raise FileNotFoundError(f"Missing route output: {path}; run calculate-landowners.py first.")
    owners = pyogrio.read_dataframe(path)
    if "Owner" not in owners.columns:
        raise RuntimeError(f"Missing Owner field in {path}")
    if owners.crs is None:
        raise RuntimeError(f"Missing coordinate reference system in {path}")
    owners["_owner_key"] = owners["Owner"].map(owner_key)
    owners = owners.loc[owners["_owner_key"].ne("")].copy()
    if owners["_owner_key"].duplicated().any():
        raise RuntimeError(f"Multiple holdings with the same normalized owner in {path}")
    return owners.to_crs("EPSG:4326").set_index("_owner_key")


def shared_landowners(
    primary: gpd.GeoDataFrame, secondary: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    shared_keys = primary.index.intersection(secondary.index)
    columns = ["Owner"]
    for label, frame in (("Primary", primary), ("Secondary", secondary)):
        columns.extend(f"{label} {column}" for column in frame.columns if column != "geometry")
    rows = []
    for key in shared_keys:
        first = primary.loc[key]
        second = secondary.loc[key]
        names = {
            name.strip()
            for value in (first["Owner"], second["Owner"])
            for name in value.split(";")
            if name.strip()
        }
        row = {"Owner": "; ".join(sorted(names))}
        for label, source in (("Primary", first), ("Secondary", second)):
            row.update(
                {f"{label} {column}": value for column, value in source.items() if column != "geometry"}
            )
        # Shared ownership can include different parcels on each route. Retain
        # both mapped holdings and dissolve any overlap without summing metrics.
        row["geometry"] = shapely.union_all(
            shapely.make_valid([first.geometry, second.geometry])
        )
        rows.append(row)
    owners = gpd.GeoDataFrame(
        rows, columns=[*columns, "geometry"], geometry="geometry", crs="EPSG:4326"
    )
    return owners.sort_values("Owner", key=lambda values: values.str.casefold()).reset_index(drop=True)


def write_outputs(owners: gpd.GeoDataFrame, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    tabular = pd.DataFrame(owners.drop(columns="geometry"))
    tabular.to_csv(output_dir / "shared-landowners.csv", index=False, encoding="utf-8-sig")
    lines = [
        "LANDOWNERS SHARED BY THE PRIMARY AND SECONDARY BRIDGER PIPELINE ROUTES",
        f"Total shared landowners: {len(tabular):,}",
        "",
    ]
    for number, row in tabular.iterrows():
        lines.append(f"{number + 1}. {row['Owner']}")
        for column, value in row.items():
            if column != "Owner" and pd.notna(value) and str(value).strip():
                lines.append(f"   {column}: {value}")
        lines.append("")
    (output_dir / "shared-landowners.txt").write_text("\n".join(lines), encoding="utf-8")
    # to_json also writes a valid empty FeatureCollection when no owners match.
    (output_dir / "shared-landowners.geojson").write_text(owners.to_json(drop_id=True), encoding="utf-8")


def main() -> int:
    args = parse_args()
    primary = read_route(args.primary_dir.resolve() / "primary-landowners.geojson")
    secondary = read_route(args.secondary_dir.resolve() / "secondary-landowners.geojson")
    owners = shared_landowners(primary, secondary)
    output_dir = args.output_dir.resolve()
    write_outputs(owners, output_dir)
    print(f"Wrote {len(owners):,} shared landowners to {output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
