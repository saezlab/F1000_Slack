import importlib
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


def install_dependency_stubs():
    pandas_stub = types.ModuleType("pandas")
    pandas_stub.isna = lambda value: value is None
    pandas_stub.DataFrame = lambda *args, **kwargs: None
    pandas_stub.read_csv = lambda *args, **kwargs: None
    sys.modules.setdefault("pandas", pandas_stub)

    fuzzywuzzy_stub = types.ModuleType("fuzzywuzzy")
    fuzzywuzzy_stub.fuzz = types.SimpleNamespace(ratio=lambda left, right: 0)
    sys.modules.setdefault("fuzzywuzzy", fuzzywuzzy_stub)

    pyzotero_stub = types.ModuleType("pyzotero")
    pyzotero_stub.zotero = types.SimpleNamespace(Zotero=lambda *args, **kwargs: None)
    sys.modules.setdefault("pyzotero", pyzotero_stub)

    slack_stub = types.ModuleType("slack_sdk")
    slack_stub.WebClient = lambda *args, **kwargs: None
    slack_errors_stub = types.ModuleType("slack_sdk.errors")

    class SlackApiError(Exception):
        def __init__(self, message="", response=None):
            super().__init__(message)
            self.response = response or {}

    slack_errors_stub.SlackApiError = SlackApiError
    sys.modules.setdefault("slack_sdk", slack_stub)
    sys.modules.setdefault("slack_sdk.errors", slack_errors_stub)


install_dependency_stubs()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
bot = importlib.import_module("post_to_slack_last_modified")


def paper(key, modified, collections=None):
    return {
        "key": key,
        "data": {
            "key": key,
            "itemType": "journalArticle",
            "dateModified": modified,
            "collections": collections or [],
            "title": f"Paper {key}",
        },
    }


def note(key, parent_key, modified):
    data = {
        "key": key,
        "itemType": "note",
        "dateModified": modified,
        "note": f"<p>Note {key}</p>",
    }
    if parent_key is not None:
        data["parentItem"] = parent_key
    return {"key": key, "data": data}


class FakeZotero:
    def __init__(self, collection_pages=None, note_pages=None, parents=None, children=None):
        self.collection_pages = collection_pages or {}
        self.note_pages = note_pages or {}
        self.parents = parents or {}
        self.children_by_key = children or {}
        self.collection_calls = []
        self.note_calls = []

    def collection_items_top(self, collection_id, **params):
        self.collection_calls.append((collection_id, params))
        return self.collection_pages.get(params.get("start", 0), [])

    def items(self, **params):
        self.note_calls.append(params)
        return self.note_pages.get(params.get("start", 0), [])

    def item(self, item_key):
        return self.parents[item_key]

    def children(self, item_key):
        return self.children_by_key.get(item_key, [])


class FakeDataFrame:
    def __init__(self, rows):
        self.rows = rows
        self.columns = list(rows[0].keys()) if rows else []
        self.written_rows = None

    def iterrows(self):
        for index, row in enumerate(self.rows):
            yield index, row

    def __setitem__(self, key, values):
        for row, value in zip(self.rows, values):
            row[key] = value

    def to_csv(self, path, index=False):
        self.written_rows = [dict(row) for row in self.rows]


class FakePandas:
    def __init__(self, frame):
        self.frame = frame

    def read_csv(self, path):
        return self.frame

    def DataFrame(self, *args, **kwargs):
        return None

    @staticmethod
    def isna(value):
        return value is None


class LastModifiedDetectionTests(unittest.TestCase):
    cutoff = "2026-04-24T00:00:00Z"

    def test_old_paper_with_new_child_note_is_detected(self):
        parent = paper("P1", "2023-01-01T00:00:00Z", ["C1"])
        zot = FakeZotero(
            note_pages={0: [note("N1", "P1", "2026-04-24T10:00:00Z")]},
            parents={"P1": parent},
        )

        pubs, latest = bot.fetch_new_publications(zot, "C1", self.cutoff)

        self.assertEqual([pub["key"] for pub in pubs], ["P1"])
        self.assertEqual(latest, datetime.fromisoformat("2026-04-24T10:00:00+00:00"))

    def test_old_paper_with_old_child_note_is_ignored(self):
        parent = paper("P1", "2023-01-01T00:00:00Z", ["C1"])
        zot = FakeZotero(
            note_pages={0: [note("N1", "P1", "2026-04-23T10:00:00Z")]},
            parents={"P1": parent},
        )

        pubs, latest = bot.fetch_new_publications(zot, "C1", self.cutoff)

        self.assertEqual(pubs, [])
        self.assertIsNone(latest)

    def test_recent_top_level_paper_is_detected(self):
        pub = paper("P1", "2026-04-24T09:00:00Z", ["C1"])
        zot = FakeZotero(collection_pages={0: [pub]})

        pubs, latest = bot.fetch_new_publications(zot, "C1", self.cutoff)

        self.assertEqual([item["key"] for item in pubs], ["P1"])
        self.assertEqual(latest, datetime.fromisoformat("2026-04-24T09:00:00+00:00"))
        self.assertEqual(zot.collection_calls[0][1]["sort"], "dateModified")

    def test_new_note_parent_outside_subcollection_is_ignored(self):
        parent = paper("P1", "2023-01-01T00:00:00Z", ["OTHER"])
        zot = FakeZotero(
            note_pages={0: [note("N1", "P1", "2026-04-24T10:00:00Z")]},
            parents={"P1": parent},
        )

        pubs, latest = bot.fetch_new_publications(zot, "C1", self.cutoff)

        self.assertEqual(pubs, [])
        self.assertIsNone(latest)

    def test_multiple_changed_notes_on_one_paper_deduplicate(self):
        parent = paper("P1", "2026-04-24T08:00:00Z", ["C1"])
        zot = FakeZotero(
            collection_pages={0: [parent]},
            note_pages={
                0: [
                    note("N1", "P1", "2026-04-24T09:00:00Z"),
                    note("N2", "P1", "2026-04-24T11:00:00Z"),
                ]
            },
            parents={"P1": parent},
        )

        pubs, latest = bot.fetch_new_publications(zot, "C1", self.cutoff)

        self.assertEqual([item["key"] for item in pubs], ["P1"])
        self.assertEqual(latest, datetime.fromisoformat("2026-04-24T11:00:00+00:00"))


class LogOnlyModeTests(unittest.TestCase):
    def test_log_only_does_not_send_but_updates_state(self):
        frame = FakeDataFrame([
            {
                "subcollectionID": "C1",
                "lastDate": "2026-04-24T00:00:00Z",
                "channel": "paper_channel",
                "receiverMails": "person@example.com",
            }
        ])
        latest = datetime.fromisoformat("2026-04-24T12:00:00+00:00")
        pub = paper("P1", "2026-04-24T12:00:00Z", ["C1"])

        def fail_if_called(*args, **kwargs):
            raise AssertionError("send path should not be called in log-only mode")

        with patch.object(bot, "pd", FakePandas(frame)), \
                patch.object(bot.zotero, "Zotero", return_value=FakeZotero()), \
                patch.object(bot, "fetch_new_publications", return_value=([pub], latest)), \
                patch.object(bot, "format_publication", return_value="formatted publication"), \
                patch.object(bot, "post_to_slack", side_effect=fail_if_called), \
                patch.object(bot.smtplib, "SMTP", side_effect=fail_if_called), \
                patch.object(sys, "argv", [
                    "post_to_slack_last_modified.py",
                    "--file_path", "state_last_modified.csv",
                    "--zotero_api_key", "zotero-key",
                    "--zotero_library_id", "123",
                    "--slack_token", "slack-token",
                    "--gmail_password", "gmail-password",
                    "--slack_ids_url", "slack-ids-url",
                    "--log_only",
                ]):
            bot.main()

        self.assertEqual(frame.rows[0]["lastDate"], "2026-04-24T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
