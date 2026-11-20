#!/usr/bin/env python3
import unittest
import urllib.parse
from email.message import Message

import run

OPERATION_NOT_ALLOWED = (
    "1730824676748658559:Double Rare Bundle:Operation Not Allowed. "
    "Cannot perform this operation on this product: the product must be in one of "
    "these statuses: DRAFT, PENDING, FAILED, ACTIVATE, SELLER_DEACTIVATED."
)

SAMPLE_ROWS = [
    {
        "id": 75326,
        "sku": "HAP_DOUBLE_RARE",
        "errorMsg": "",
        "errorMsgList": [{"error": OPERATION_NOT_ALLOWED}],
    },
    {
        "id": 7819,
        "sku": "HAP_RARE",
        "errorMsg": "",
        "errorMsgList": [{"error": "1730824676748658559:Rare Bundle:Operation Not Allowed."}],
    },
    {
        "id": 7818,
        "sku": "HAP_STARTER",
        "errorMsgList": ["Starter Bundle:Operation Not Allowed."],
    },
    {
        "id": 7817,
        "sku": "HAP_LOOT",
        "errorMsg": "Booster Bundle:Operation Not Allowed.",
        "errorMsgList": [],
    },
]


class ClassifyTests(unittest.TestCase):
    def test_sample_failures_are_not_pushed(self):
        matched, skipped = run.classify(SAMPLE_ROWS)
        self.assertEqual(matched, [])
        self.assertEqual([row["id"] for row in skipped], [75326, 7819, 7818, 7817])

    def test_rate_limit_phrases_match_case_insensitively(self):
        rows = [
            {"id": 1, "sku": "A", "errorMsgList": [{"error": "Too many requests. Try again."}]},
            {"id": 2, "sku": "B", "errorMsg": "Request frequency exceeded"},
            {"id": 3, "sku": "C", "errorMsgList": [{"error": "shop request limit reached"}]},
            {"id": 4, "sku": "D", "errorMsgList": ["try again in 30 Seconds"]},
        ]
        matched, skipped = run.classify(rows)
        self.assertEqual(skipped, [])
        self.assertEqual([row["id"] for row, _needle, _text in matched], [1, 2, 3, 4])
        self.assertEqual([needle for _row, needle, _text in matched], [
            "too many requests",
            "exceeded",
            "limit",
            "seconds",
        ])

    def test_duplicate_ids_are_pushed_once(self):
        rows = [
            {"id": 9, "sku": "A", "errorMsg": "too many requests"},
            {"id": 9, "sku": "A", "errorMsg": "too many requests"},
        ]
        matched, skipped = run.classify(rows)
        self.assertEqual(len(matched), 1)
        self.assertEqual(skipped, [])

    def test_push_form_comma_separates_ids(self):
        encoded = urllib.parse.urlencode(run.push_form([75326, 7819, 7818, 7817]))
        self.assertEqual(encoded, "syncType=1&syncDetailsIds=75326%2C7819%2C7818%2C7817")


class CookieTests(unittest.TestCase):
    def test_parse_strips_prefix_and_quotes(self):
        jar = run.parse_cookie('Cookie: "JSESSIONID=abc; muc_token=def="')
        self.assertEqual(jar, {"JSESSIONID": "abc", "muc_token": "def="})

    def test_set_cookie_updates_existing_name(self):
        jar = {"JSESSIONID": "old", "muc_token": "keep"}
        headers = Message()
        headers.add_header("Set-Cookie", "JSESSIONID=new; Path=/; HttpOnly")
        self.assertTrue(run.apply_set_cookie(jar, headers))
        self.assertEqual(jar["JSESSIONID"], "new")
        self.assertEqual(jar["muc_token"], "keep")


class ResponseTests(unittest.TestCase):
    def test_html_and_401_are_logged_out(self):
        self.assertTrue(run.session_dead(401, b"nope", None))
        self.assertTrue(run.session_dead(200, b"<html>login</html>", None))
        self.assertFalse(run.session_dead(200, b'{"code":0}', {"code": 0, "msg": "Successfully"}))

    def test_non_json_html_does_not_decode(self):
        self.assertIsNone(run.decode_payload(b"<!doctype html><html>login</html>"))


if __name__ == "__main__":
    unittest.main()
