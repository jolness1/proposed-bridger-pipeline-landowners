import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box


spec = importlib.util.spec_from_file_location(
    "common_landowners", Path(__file__).resolve().parents[1] / "common-landowners.py"
)
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)


class SharedLandownersTests(unittest.TestCase):
    def write_route(self, directory, names, geometries, counts):
        directory.mkdir()
        frame = gpd.GeoDataFrame(
            {"Owner": names, "Parcel Count": counts},
            geometry=geometries,
            crs="EPSG:4326",
        )
        path = directory / f"{directory.name}-landowners.geojson"
        path.write_text(frame.to_json(drop_id=True))
        return common.read_route(path)

    def test_matching_variants_excludes_unnamed_and_preserves_route_details(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_geometry = box(0, 0, 2, 2)
            second_geometry = box(1, 0, 3, 2)
            primary = self.write_route(
                root / "primary",
                ["C & S LLC; C&S LLC", common.UNNAMED_OWNER, "Primary Only"],
                [first_geometry, box(5, 5, 6, 6), box(8, 8, 9, 9)],
                [2, 1, 1],
            )
            secondary = self.write_route(
                root / "secondary",
                ["c and s llc", common.UNNAMED_OWNER, "Secondary Only"],
                [second_geometry, box(5, 5, 6, 6), box(10, 10, 11, 11)],
                [3, 1, 1],
            )
            shared = common.shared_landowners(primary, secondary)
            self.assertEqual(len(shared), 1)
            self.assertEqual(shared.iloc[0]["Primary Parcel Count"], 2)
            self.assertEqual(shared.iloc[0]["Secondary Parcel Count"], 3)
            self.assertTrue(shared.geometry.iloc[0].equals(first_geometry.union(second_geometry)))
            common.write_outputs(shared, root / "shared")
            csv = pd.read_csv(root / "shared/shared-landowners.csv")
            geojson = json.loads((root / "shared/landowners.geojson").read_text())
            self.assertEqual(csv.to_dict("records"), [feature["properties"] for feature in geojson["features"]])
            self.assertIn("Total shared landowners: 1", (root / "shared/shared-landowners.txt").read_text())

    def test_no_matches_writes_empty_outputs_with_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = self.write_route(root / "primary", ["Alice"], [box(0, 0, 1, 1)], [1])
            secondary = self.write_route(root / "secondary", ["Bob"], [box(0, 0, 1, 1)], [1])
            shared = common.shared_landowners(primary, secondary)
            common.write_outputs(shared, root / "shared")
            csv = pd.read_csv(root / "shared/shared-landowners.csv")
            self.assertTrue(csv.empty)
            self.assertIn("Primary Parcel Count", csv.columns)
            geojson = json.loads((root / "shared/landowners.geojson").read_text())
            self.assertEqual(geojson["features"], [])

    def test_blank_owner_names_are_not_match_keys(self):
        for name in (None, "", "  ", common.UNNAMED_OWNER):
            self.assertEqual(common.owner_key(name), "")

    def test_missing_input_names_the_missing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(FileNotFoundError, "landowners.geojson"):
                common.read_route(Path(temporary) / "primary-landowners.geojson")


if __name__ == "__main__":
    unittest.main()
