#!/usr/bin/env python3
"""Find connected landowner holdings crossed by the proposed Bridger pipeline."""

from __future__ import annotations

import argparse
import gc
import re
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PARCELS = (
    PROJECT_ROOT / "input/cadastral/Montana_Cadastral/OWNERPARCEL.shp"
)
SQ_METERS_PER_ACRE = 4_046.8564224
METERS_PER_MILE = 1_609.344


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find cadastral parcels crossed by the pipeline and every contiguous "
            "same-owner parcel connected to them."
        )
    )
    parser.add_argument(
        "--pipeline",
        type=Path,
        help="Pipeline .shp path (default: the only .shp in input/shapefile).",
    )
    parser.add_argument(
        "--parcels",
        type=Path,
        default=DEFAULT_PARCELS,
        help=f"Owner parcel .shp path (default: {DEFAULT_PARCELS.relative_to(PROJECT_ROOT)}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "output",
        help="Output directory (default: output).",
    )
    parser.add_argument(
        "--adjacency-tolerance",
        type=float,
        default=0.5,
        metavar="METERS",
        help=(
            "Maximum gap between same-owner parcels that counts as attached "
            "(default: 0.5 meters)."
        ),
    )
    parser.add_argument(
        "--pipeline-buffer",
        type=float,
        default=0.0,
        metavar="METERS",
        help=(
            "Optional buffer used only to decide which parcels the pipeline crosses "
            "(default: 0 meters, an exact intersection)."
        ),
    )
    args = parser.parse_args()
    if args.adjacency_tolerance < 0 or args.pipeline_buffer < 0:
        parser.error("distance values cannot be negative")
    return args


def find_pipeline(path: Path | None) -> Path:
    if path is not None:
        return path.resolve()
    candidates = sorted((PROJECT_ROOT / "input/shapefile").glob("*.shp"))
    if len(candidates) != 1:
        raise RuntimeError(
            "Expected exactly one pipeline .shp in input/shapefile; "
            "pass --pipeline explicitly."
        )
    return candidates[0]


def require_inputs(*paths: Path) -> None:
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing input file(s): " + ", ".join(missing))


def normalize_owner(series: pd.Series) -> pd.Series:
    """Make harmless punctuation/spacing variants compare as the same owner."""
    return (
        series.fillna("")
        .astype(str)
        .str.upper()
        .str.replace("&", " AND ", regex=False)
        .str.replace(r"[^A-Z0-9]+", " ", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def read_pipeline_hits(
    pipeline_path: Path, parcels_path: Path, pipeline_buffer: float
) -> tuple[gpd.GeoDataFrame, shapely.Geometry]:
    parcel_info = pyogrio.read_info(parcels_path)
    parcel_crs = parcel_info["crs"]
    if not parcel_crs:
        raise RuntimeError("The cadastral layer has no coordinate reference system.")

    pipeline = pyogrio.read_dataframe(pipeline_path)
    if pipeline.crs is None:
        raise RuntimeError("The pipeline layer has no coordinate reference system.")
    if pipeline.empty:
        raise RuntimeError("The pipeline layer contains no features.")
    pipeline = pipeline.to_crs(parcel_crs)
    pipeline_geometry = pipeline.geometry.union_all()
    search_geometry = (
        pipeline_geometry.buffer(pipeline_buffer)
        if pipeline_buffer
        else pipeline_geometry
    )

    minx, miny, maxx, maxy = search_geometry.bounds
    # The shapefile's .sbn index makes this much cheaper than reading all geometry.
    candidates = pyogrio.read_dataframe(
        parcels_path,
        bbox=(minx, miny, maxx, maxy),
        fid_as_index=True,
    )
    hits = candidates.loc[candidates.geometry.intersects(search_geometry)].copy()
    if hits.empty:
        raise RuntimeError("No cadastral parcels intersect the pipeline.")
    hits.index.name = "fid"
    hits["_owner_key"] = normalize_owner(hits["OwnerName"])
    unnamed = hits["_owner_key"].eq("")
    hits.loc[unnamed, "_owner_key"] = [
        f"__OWNER_NOT_LISTED_FID_{fid}" for fid in hits.index[unnamed]
    ]
    return hits, pipeline_geometry


def read_relevant_owner_parcels(
    parcels_path: Path, hits: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    hit_owner_keys = {
        key for key in hits["_owner_key"].unique() if not key.startswith("__")
    }

    print("Scanning owner names to include spelling/punctuation variants ...", flush=True)
    owner_names = pyogrio.read_dataframe(
        parcels_path,
        columns=["OwnerName"],
        read_geometry=False,
    )
    owner_names["_owner_key"] = normalize_owner(owner_names["OwnerName"])
    source_names = sorted(
        owner_names.loc[
            owner_names["_owner_key"].isin(hit_owner_keys), "OwnerName"
        ]
        .dropna()
        .unique()
        .tolist()
    )
    del owner_names
    gc.collect()

    if source_names:
        where = "OwnerName IN (" + ", ".join(map(sql_string, source_names)) + ")"
        named = pyogrio.read_dataframe(
            parcels_path,
            where=where,
            fid_as_index=True,
        )
        named.index.name = "fid"
    else:
        named = gpd.GeoDataFrame(columns=hits.columns, geometry="geometry", crs=hits.crs)

    # Appending all direct hits retains parcels whose owner field is blank. FID is
    # stable in this shapefile, so it is also a reliable deduplication key.
    relevant = pd.concat(
        [named.reset_index(), hits.reset_index()], ignore_index=True
    ).drop_duplicates(subset="fid", keep="first")
    relevant = gpd.GeoDataFrame(relevant, geometry="geometry", crs=hits.crs)
    relevant["_owner_key"] = normalize_owner(relevant["OwnerName"])
    unnamed = relevant["_owner_key"].eq("")
    relevant.loc[unnamed, "_owner_key"] = [
        f"__OWNER_NOT_LISTED_FID_{fid}" for fid in relevant.loc[unnamed, "fid"]
    ]
    relevant["_pipeline_hit"] = relevant["fid"].isin(set(hits.index))
    return relevant


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = np.arange(size, dtype=np.int64)
        self.rank = np.zeros(size, dtype=np.int8)

    def find(self, item: int) -> int:
        root = item
        while self.parent[root] != root:
            root = int(self.parent[root])
        while self.parent[item] != item:
            next_item = int(self.parent[item])
            self.parent[item] = root
            item = next_item
        return root

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def connected_to_pipeline(
    relevant: gpd.GeoDataFrame, tolerance: float
) -> gpd.GeoDataFrame:
    selected_groups: list[gpd.GeoDataFrame] = []
    groups = relevant.groupby("_owner_key", sort=False)
    for number, (_, owner_parcels) in enumerate(groups, start=1):
        owner_parcels = owner_parcels.reset_index(drop=True)
        seed_positions = np.flatnonzero(owner_parcels["_pipeline_hit"].to_numpy())
        if not len(seed_positions):
            continue

        union_find = UnionFind(len(owner_parcels))
        pairs = owner_parcels.sindex.query(
            owner_parcels.geometry,
            predicate="dwithin",
            distance=tolerance,
        )
        for left, right in zip(pairs[0], pairs[1], strict=True):
            if left < right:
                union_find.union(int(left), int(right))

        seed_roots = {union_find.find(int(position)) for position in seed_positions}
        keep = [union_find.find(position) in seed_roots for position in range(len(owner_parcels))]
        selected_groups.append(owner_parcels.loc[keep])
        if number % 50 == 0:
            print(f"  checked {number:,} of {groups.ngroups:,} owners", flush=True)

    if not selected_groups:
        raise RuntimeError("No connected owner holdings were selected.")
    return gpd.GeoDataFrame(
        pd.concat(selected_groups, ignore_index=True),
        geometry="geometry",
        crs=relevant.crs,
    )


def clean(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def joined(values: pd.Series, *, separator: str = "; ") -> str:
    unique = sorted({text for value in values for text in [clean(value)] if text})
    return separator.join(unique)


def joined_addresses(frame: pd.DataFrame, columns: list[str]) -> str:
    addresses = set()
    for row in frame[columns].itertuples(index=False, name=None):
        parts = [text for value in row for text in [clean(value)] if text]
        if parts:
            addresses.add(", ".join(parts))
    return "; ".join(sorted(addresses))


def township_range_sections(frame: pd.DataFrame) -> str:
    descriptions = set()
    for township, range_, section in frame[["Township", "Range", "Section"]].itertuples(
        index=False, name=None
    ):
        parts = []
        if clean(township):
            parts.append(f"T {clean(township)}")
        if clean(range_):
            parts.append(f"R {clean(range_)}")
        if clean(section):
            parts.append(f"Sec {clean(section)}")
        if parts:
            descriptions.add(" / ".join(parts))
    return "; ".join(sorted(descriptions))


def joined_money(values: pd.Series) -> str:
    numbers = sorted({int(value) for value in values if pd.notna(value) and int(value) != 0})
    return "; ".join(f"${number:,}" for number in numbers)


def make_owner_features(
    selected: gpd.GeoDataFrame, pipeline_geometry: shapely.Geometry
) -> gpd.GeoDataFrame:
    rows: list[dict[str, object]] = []
    for owner_key, frame in selected.groupby("_owner_key", sort=False):
        geometries = shapely.make_valid(frame.geometry.to_numpy())
        geometry = shapely.union_all(geometries, grid_size=0.01)
        owner = joined(frame["OwnerName"])
        if owner_key.startswith("__"):
            owner = "Owner not listed in cadastral data"

        rows.append(
            {
                "Owner": owner,
                "DBA": joined(frame["DbaName"]),
                "Care Of": joined(frame["CareOfTaxp"]),
                "Mailing Address": joined_addresses(
                    frame,
                    [
                        "OwnerAddre",
                        "OwnerAdd_1",
                        "OwnerAdd_2",
                        "OwnerCity",
                        "OwnerState",
                        "OwnerZipCo",
                    ],
                ),
                "Counties": joined(frame["CountyName"]),
                "Property Addresses": joined_addresses(
                    frame, ["AddressLin", "AddressL_1", "CityStateZ"]
                ),
                "Property Access": joined(frame["PropAccess"]),
                "Property Types": joined(frame["PropType"]),
                "Parcel IDs": joined(frame["PARCELID"]),
                "Property IDs": joined(frame["PropertyID"]),
                "Assessment Codes": joined(frame["Assessment"]),
                "Legal Descriptions": joined(frame["LegalDescr"]),
                "Township / Range / Section": township_range_sections(frame),
                "Tax Years": joined(frame["TaxYear"]),
                "Assessed Total Values": joined_money(frame["TotalValue"]),
                "Parcel Count": int(len(frame)),
                "Pipeline Parcel Count": int(frame["_pipeline_hit"].sum()),
                "Cadastral GIS Acres": round(float(frame["GISAcres"].fillna(0).sum()), 2),
                "Mapped Holding Acres": round(float(geometry.area / SQ_METERS_PER_ACRE), 2),
                "Pipeline Miles on Holding": round(
                    float(pipeline_geometry.intersection(geometry).length / METERS_PER_MILE),
                    3,
                ),
                "geometry": geometry,
            }
        )

    owners = gpd.GeoDataFrame(rows, geometry="geometry", crs=selected.crs)
    owners = owners.sort_values(["Owner", "Counties"], key=lambda col: col.astype(str).str.casefold())
    return owners.reset_index(drop=True)


def write_outputs(owners: gpd.GeoDataFrame, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "landowners.csv"
    txt_path = output_dir / "landowners.txt"
    geojson_path = output_dir / "landowners.geojson"

    tabular = pd.DataFrame(owners.drop(columns="geometry"))
    tabular.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines = [
        "LANDOWNERS ALONG THE PROPOSED BRIDGER PIPELINE",
        f"Total owner holdings: {len(tabular):,}",
        "",
    ]
    for number, row in tabular.iterrows():
        lines.append(f"{number + 1}. {row['Owner']}")
        for column, value in row.items():
            if column == "Owner" or clean(value) == "":
                continue
            lines.append(f"   {column}: {value}")
        lines.append("")
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    # RFC 7946 GeoJSON coordinates are longitude/latitude (WGS 84).
    owners.to_crs("EPSG:4326").to_file(
        geojson_path,
        driver="GeoJSON",
        engine="pyogrio",
        index=False,
    )


def main() -> int:
    args = parse_args()
    pipeline_path = find_pipeline(args.pipeline)
    parcels_path = args.parcels.resolve()
    output_dir = args.output_dir.resolve()
    require_inputs(pipeline_path, parcels_path)

    print(f"Pipeline: {pipeline_path}", flush=True)
    print(f"Parcels:  {parcels_path}", flush=True)
    hits, pipeline_geometry = read_pipeline_hits(
        pipeline_path, parcels_path, args.pipeline_buffer
    )
    named_count = hits.loc[hits["OwnerName"].notna(), "OwnerName"].nunique()
    unnamed_count = int(hits["OwnerName"].isna().sum())
    print(
        f"Direct intersections: {len(hits):,} parcels, {named_count:,} named owners, "
        f"{unnamed_count:,} parcels without a listed owner",
        flush=True,
    )

    relevant = read_relevant_owner_parcels(parcels_path, hits)
    print(
        f"Testing connectivity among {len(relevant):,} parcels belonging to hit owners ...",
        flush=True,
    )
    selected = connected_to_pipeline(relevant, args.adjacency_tolerance)
    print(f"Selected {len(selected):,} connected parcels; dissolving by owner ...", flush=True)
    owners = make_owner_features(selected, pipeline_geometry)
    write_outputs(owners, output_dir)
    print(f"Wrote {len(owners):,} owner holdings to {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
