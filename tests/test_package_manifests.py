import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


class PackageManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src_dir = Path(__file__).resolve().parents[1] / "ros2_ws" / "src"
        cls.manifests = sorted(cls.src_dir.glob("*/package.xml"))

    def test_workspace_contains_nine_valid_package_manifests(self):
        self.assertEqual(len(self.manifests), 9)
        for manifest in self.manifests:
            with self.subTest(manifest=manifest):
                ET.parse(manifest)

    def test_ament_python_is_a_build_type_not_a_rosdep_dependency(self):
        python_packages = 0
        for manifest in self.manifests:
            root = ET.parse(manifest).getroot()
            build_type = root.findtext("./export/build_type")
            if build_type != "ament_python":
                continue
            python_packages += 1
            dependency_names = {
                (element.text or "").strip()
                for element in root
                if element.tag.endswith("_depend") or element.tag == "depend"
            }
            with self.subTest(package=root.findtext("name")):
                self.assertNotIn("ament_python", dependency_names)
        self.assertEqual(python_packages, 8)


if __name__ == "__main__":
    unittest.main()
