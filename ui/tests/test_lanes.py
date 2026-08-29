from unittest import TestCase
from sorter.lanes import ensure_sort_folder


class EnsureSortFolderTests(TestCase):
    def test_artist_leaf_stays_exact(self) -> None:
        self.assertEqual(ensure_sort_folder("Paolo Mac", "pink"), "Paolo Mac")
        self.assertEqual(ensure_sort_folder("House+Zouk", "green"), "House+Zouk")
        self.assertEqual(ensure_sort_folder("R&B", "blue"), "R&B")

    def test_energy_chill_roots_deepen(self) -> None:
        self.assertEqual(ensure_sort_folder("Energy", "pink"), "Energy/Light")
        self.assertEqual(ensure_sort_folder("Chill", "pink"), "Chill/Lounge")
        self.assertEqual(ensure_sort_folder("Energy/Dark"), "Energy/Dark")
