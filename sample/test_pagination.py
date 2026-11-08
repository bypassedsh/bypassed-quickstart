import unittest

from pagination import page_count, page_slice


class PageSliceTest(unittest.TestCase):
    def test_first_page(self):
        self.assertEqual(page_slice([1, 2, 3, 4, 5], 1, 2), [1, 2])

    def test_last_partial_page(self):
        self.assertEqual(page_slice([1, 2, 3, 4, 5], 3, 2), [5])

    def test_past_the_end(self):
        self.assertEqual(page_slice([1, 2, 3], 3, 2), [])

    def test_rejects_page_zero(self):
        with self.assertRaises(ValueError):
            page_slice([1], 0, 10)


class PageCountTest(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(page_count(0, 10), 0)
        self.assertEqual(page_count(10, 10), 1)
        self.assertEqual(page_count(11, 10), 2)


if __name__ == "__main__":
    unittest.main()
