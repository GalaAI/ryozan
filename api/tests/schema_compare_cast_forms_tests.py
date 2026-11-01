"""Equivalent IN-list casts must not read as schema drift.

Postgres prints the same check differently when it comes from a model's DDL
and when it comes back from a dump and restore. A studio adopting a restored
database must not be refused over text that means the same thing.
"""

import unittest

from app.core.schema_compare import canonical_definition, compare_schemas

FROM_MODEL = (
    "CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'it''s'::character varying])::text[])))"
)
FROM_RESTORE = (
    "CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('it''s'::character varying)::text])))"
)


def _snapshot(constraint_definition: str, index_definition: str) -> dict:
    return {
        "tables": {
            "ryozan_things": {
                "columns": {},
                "indexes": {"ux_things": {"definition": index_definition, "valid": True}},
                "constraints": {"ck_things": {"definition": constraint_definition, "kind": "c"}},
                "triggers": {},
            }
        }
    }


class CastFormTests(unittest.TestCase):
    def test_both_forms_canonicalize_to_one_definition(self):
        self.assertEqual(canonical_definition(FROM_MODEL), canonical_definition(FROM_RESTORE))

    def test_other_definitions_are_untouched(self):
        plain = "CHECK ((status)::text = 'pending'::text)"
        self.assertEqual(canonical_definition(plain), plain)

    def test_equivalent_constraint_and_index_are_not_differences(self):
        index_model = f"CREATE UNIQUE INDEX ux_things ON public.ryozan_things USING btree (id) WHERE {FROM_MODEL[6:-1]}"
        index_restore = (
            f"CREATE UNIQUE INDEX ux_things ON public.ryozan_things USING btree (id) WHERE {FROM_RESTORE[6:-1]}"
        )
        diffs = compare_schemas(_snapshot(FROM_MODEL, index_model), _snapshot(FROM_RESTORE, index_restore))
        self.assertEqual(diffs, [])

    def test_a_real_change_is_still_reported(self):
        changed = FROM_RESTORE.replace("pending", "paid")
        diffs = compare_schemas(_snapshot(FROM_MODEL, "x"), _snapshot(changed, "x"))
        self.assertEqual([d.kind for d in diffs], ["constraint_definition"])


if __name__ == "__main__":
    unittest.main()
