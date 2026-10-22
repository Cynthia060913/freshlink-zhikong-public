"""C-only preview filtering tests against fixed acceptance vectors."""

import json
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.modules.telemetry.quality import process_batch
from app.modules.telemetry.query_preview import (
    page_preview_by_sampled_at,
    select_preview_observations,
    sort_preview_by_sampled_at,
)

_EXAMPLES = Path(__file__).resolve().parents[3] / "docs" / "modules" / "C" / "examples"


def _fixture() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    proposal = json.loads(
        (_EXAMPLES / "telemetry-contract-proposal.json").read_text(encoding="utf-8")
    )
    acceptance = json.loads(
        (_EXAMPLES / "query-csv-acceptance.json").read_text(encoding="utf-8")
    )
    results = process_batch(
        proposal["a_to_c"]["observations"], proposal["fixture_threshold_c"]
    )
    for result, row in zip(results, acceptance["assigned_rows"], strict=True):
        result["observation_id"] = row["observation_id"]
    return results, acceptance


class TelemetryQueryPreviewTests(unittest.TestCase):
    def test_fixed_filter_matches_page_and_csv_expected_ids(self) -> None:
        results, acceptance = _fixture()
        scenario = acceptance["scenarios"]["same_filter_page_and_csv"]
        selected = select_preview_observations(results, scenario["filters"])
        ids = [row["observation_id"] for row in selected]
        self.assertEqual(ids, scenario["csv"]["expected_observation_ids"])
        self.assertEqual(len(ids), scenario["csv"]["expected_record_count"])
        pages = [ids[index : index + 1] for index in range(0, len(ids), 1)]
        self.assertEqual(pages, scenario["page"]["expected_pages"])
        self.assertEqual(
            ids,
            [
                row["observation_id"]
                for row in select_preview_observations(
                    results, scenario["csv"]["filters"]
                )
            ],
        )
        self.assertEqual(
            [row["risk"]["state"] for row in selected], ["normal", "candidate"]
        )

    def test_invalid_sample_time_is_findable_by_quality_only(self) -> None:
        results, acceptance = _fixture()
        scenario = acceptance["scenarios"]["invalid_sample_time_quality_only"]
        selected = select_preview_observations(results, scenario["filters"])
        self.assertEqual(
            [row["observation_id"] for row in selected],
            scenario["expected_observation_ids"],
        )
        self.assertEqual(selected[0]["risk"]["state"], "unknown")
        self.assertEqual(selected[0]["raw"]["sampled_at"], "2026-10-10 00:03:00")
        self.assertEqual(
            select_preview_observations(
                results,
                {
                    **scenario["filters"],
                    "sampled_from": "2026-10-10T00:00:00Z",
                },
            ),
            [],
        )
        self.assertEqual(
            len(select_preview_observations(results, {})),
            acceptance["scenarios"]["all_raw"]["expected_record_count"],
        )

    def test_right_time_edge_is_exclusive(self) -> None:
        results, acceptance = _fixture()
        raw = deepcopy(results[0]["raw"])
        raw["message_id"] = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        raw["sampled_at"] = "2026-10-10T00:03:00Z"
        boundary = process_batch([raw], 8)[0]
        boundary["observation_id"] = "00000000-0000-4000-8000-000000000008"
        self.assertEqual(boundary["quality"], ["ok"])
        filters = acceptance["scenarios"]["same_filter_page_and_csv"]["filters"]
        selected = select_preview_observations([*results, boundary], filters)
        self.assertNotIn(
            boundary["observation_id"], [r["observation_id"] for r in selected]
        )
        wider = select_preview_observations(
            [*results, boundary],
            {**filters, "sampled_to": "2026-10-10T00:03:01Z"},
        )
        self.assertIn(boundary["observation_id"], [r["observation_id"] for r in wider])

    def test_received_window_finds_invalid_sampled_time(self) -> None:
        results, acceptance = _fixture()
        selected = select_preview_observations(
            results,
            {
                "quality_in": ["invalid_time:sampled_at"],
                "received_from": "2026-10-10T00:03:00Z",
                "received_to": "2026-10-10T00:04:00Z",
            },
        )
        self.assertEqual(
            [row["observation_id"] for row in selected],
            acceptance["scenarios"]["invalid_sample_time_quality_only"][
                "expected_observation_ids"
            ],
        )
        self.assertEqual(selected[0]["raw"]["sampled_at"], "2026-10-10 00:03:00")
        self.assertEqual(
            select_preview_observations(
                results,
                {
                    "quality_in": ["invalid_time:sampled_at"],
                    "received_from": "2026-10-10T00:03:01Z",
                },
            ),
            [selected[0]],
        )
        self.assertEqual(
            select_preview_observations(
                results,
                {
                    "quality_in": ["invalid_time:sampled_at"],
                    "received_to": "2026-10-10T00:03:01Z",
                },
            ),
            [],
        )

    def test_sampled_and_received_windows_are_intersected(self) -> None:
        results, _ = _fixture()
        selected = select_preview_observations(
            results,
            {
                "quality_in": ["ok"],
                "sampled_from": "2026-10-10T00:00:00Z",
                "sampled_to": "2026-10-10T00:03:00Z",
                "received_from": "2026-10-10T00:01:00Z",
                "received_to": "2026-10-10T00:02:00Z",
            },
        )
        self.assertEqual(
            [row["raw"]["message_id"] for row in selected],
            ["44444444-4444-4444-8444-444444444444"],
        )
        self.assertEqual(selected[0]["risk"]["state"], "candidate")

    def test_invalid_received_time_is_excluded_only_by_received_window(self) -> None:
        results, _ = _fixture()
        raw = deepcopy(results[0]["raw"])
        raw["message_id"] = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        raw["received_at"] = "invalid"
        invalid = process_batch([raw], 8)[0]
        self.assertIn("invalid_time:received_at", invalid["quality"])
        self.assertEqual(
            select_preview_observations(
                [invalid], {"quality_in": ["invalid_time:received_at"]}
            ),
            [invalid],
        )
        self.assertEqual(
            select_preview_observations(
                [invalid], {"received_from": "2026-10-10T00:00:00Z"}
            ),
            [],
        )

    def test_result_and_raw_are_not_mutated_by_preview_selection(self) -> None:
        results, _ = _fixture()
        selected = select_preview_observations(results, {"quality_in": ["ok"]})
        selected[0]["raw"]["metrics"][0]["value"] = 99
        self.assertEqual(results[0]["raw"]["metrics"][0]["value"], 4)
        self.assertEqual(results[0]["risk"]["state"], "normal")

    def test_sampled_sort_is_stable_and_places_unknown_time_last(self) -> None:
        results, acceptance = _fixture()
        shuffled = [results[index] for index in (5, 3, 2, 0, 4, 1, 6)]
        ordered = sort_preview_by_sampled_at(shuffled)
        expected = [results[index]["observation_id"] for index in (0, 1, 2, 3, 4, 6, 5)]
        self.assertEqual([row["observation_id"] for row in ordered], expected)
        self.assertEqual(
            ordered[-1]["observation_id"],
            acceptance["assigned_rows"][5]["observation_id"],
        )
        self.assertEqual(ordered[-1]["raw"]["sampled_at"], "2026-10-10 00:03:00")
        ordered[0]["raw"]["metrics"][0]["value"] = 99
        self.assertEqual(results[0]["raw"]["metrics"][0]["value"], 4)

    def test_sampled_sort_rejects_missing_or_reused_observation_id(self) -> None:
        results, _ = _fixture()
        no_id = deepcopy(results[0])
        del no_id["observation_id"]
        for invalid in ([no_id], [results[0], results[0]]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                sort_preview_by_sampled_at(invalid)

    def test_bad_filters_fail_explicitly(self) -> None:
        results, _ = _fixture()
        invalid = (
            {"device_id": "not-a-uuid"},
            {"sampled_from": "2026-10-10T08:00:00+08:00"},
            {
                "sampled_from": "2026-10-10T00:03:00Z",
                "sampled_to": "2026-10-10T00:03:00Z",
            },
            {"received_from": "2026-10-10T08:00:00+08:00"},
            {
                "received_from": "2026-10-10T00:03:00Z",
                "received_to": "2026-10-10T00:03:00Z",
            },
            {"quality_in": "ok"},
            {"quality_in": []},
            {"unapproved": "ignored"},
        )
        for filters in invalid:
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                select_preview_observations(results, filters)

    def test_preview_pages_cover_all_rows_once_and_keep_unknown_last(self) -> None:
        results, _ = _fixture()
        shuffled = [results[index] for index in (5, 3, 2, 0, 4, 1, 6)]
        expected = sort_preview_by_sampled_at(shuffled)
        seen: list[dict[str, Any]] = []
        after = None
        while True:
            page, after = page_preview_by_sampled_at(
                shuffled, limit=2, after_observation_id=after
            )
            self.assertLessEqual(len(page), 2)
            seen.extend(page)
            if after is None:
                break
        self.assertEqual(
            [row["observation_id"] for row in seen],
            [row["observation_id"] for row in expected],
        )
        self.assertEqual(len({row["observation_id"] for row in seen}), len(results))
        self.assertEqual(seen[-1]["risk"]["state"], "unknown")
        self.assertEqual(seen[-1]["raw"]["sampled_at"], "2026-10-10 00:03:00")
        seen[0]["raw"]["metrics"][0]["value"] = 99
        self.assertEqual(results[0]["raw"]["metrics"][0]["value"], 4)

    def test_preview_page_rejects_bad_limit_and_foreign_marker(self) -> None:
        results, _ = _fixture()
        for limit in (0, 101, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                page_preview_by_sampled_at(results, limit=limit)
        for marker in ("not-a-uuid", "ffffffff-ffff-4fff-8fff-ffffffffffff"):
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                page_preview_by_sampled_at(
                    results, limit=2, after_observation_id=marker
                )

    def test_preview_page_after_last_row_is_empty(self) -> None:
        results, _ = _fixture()
        last = sort_preview_by_sampled_at(results)[-1]["observation_id"]
        page, next_after = page_preview_by_sampled_at(
            results, limit=2, after_observation_id=last
        )
        self.assertEqual(page, [])
        self.assertIsNone(next_after)


if __name__ == "__main__":
    unittest.main()
