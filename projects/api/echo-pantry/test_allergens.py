import unittest
from werkzeug.exceptions import BadRequest
from listing_review import allergen_declaration


class AllergenTests(unittest.TestCase):
    def test_choices_are_serialized_for_existing_listing_details(self):
        self.assertEqual(allergen_declaration(dict(allergen_choices=["Eggs", "Milk"])), "Milk, Eggs")
        self.assertEqual(allergen_declaration(dict(allergen_choices=["None"])), "None")
        self.assertEqual(allergen_declaration(dict(allergen_choices=["Unknown"])), "Unknown")
        self.assertEqual(allergen_declaration(dict(allergen_choices=["Milk", "Other"],
            allergen_other="Mustard, lupin")), "Milk, Other: Mustard, lupin")

    def test_invalid_selections_and_other_description_are_rejected(self):
        for choices, other in [
            ([], ""), (["None", "Milk"], ""), (["Unknown", "None"], ""),
            (["Unknown", "Eggs"], ""), (["Not a chip"], ""), (["Milk", "Milk"], ""),
            (["Other"], ""), (["Other"], " "), (["Other"], "a" * 51),
            (["Milk"], "Mustard"), ([{}], ""), ("Milk", ""), (["Other"], None),
        ]:
            with self.subTest(choices=choices, other=other), self.assertRaises(BadRequest):
                allergen_declaration(dict(allergen_choices=choices, allergen_other=other))
        self.assertEqual(allergen_declaration(dict(allergen_choices=["Other"],
            allergen_other="a" * 50)), "Other: " + "a" * 50)

    def test_existing_free_text_is_not_reinterpreted(self):
        for text in ["May contain lupin & milk", "No known allergens.", "Milk, Eggs"]:
            self.assertEqual(allergen_declaration(dict(allergens=text)), text)
