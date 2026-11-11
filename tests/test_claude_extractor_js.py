"""DOM-level checks for the Claude usage extractor.

The extractor is JavaScript injected into the webview, so the Python provider
tests exercise only its *output* shape. These tests run the real script under
Node against a mocked usage dialog, which is the only place row-matching bugs
can be caught. Skipped when Node is unavailable; GitHub's runners all ship it.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from aigauge.providers.claude import EXTRACTOR_JS

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js not available"
)

HARNESS = """
const fs = require('fs');
const nodes = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const elements = nodes.map(([text, height]) => ({
  textContent: text,
  getBoundingClientRect: () => ({ height }),
}));
global.document = {
  body: { textContent: nodes[0][0] },
  title: 'Claude',
  querySelectorAll: (sel) => (sel.includes('a[href') ? [] : elements),
  querySelector: () => null,
};
global.location = {
  href: 'https://claude.ai/settings/usage',
  pathname: '/settings/usage',
  hash: '',
  hostname: 'claude.ai',
};
const result = eval(fs.readFileSync(process.argv[3], 'utf8'));
process.stdout.write(JSON.stringify(result));
"""

SESSION = "Current session Resets in 3 hr 10 min 18% used"
ALL_MODELS = "All models Resets in 22 hr 0 min 2% used"
FABLE = "Fable Resets in 22 hr 0 min 4% used"
IDLE_SESSION = "Current session Starts when a message is sent 0% used"
NEW_IDLE_SESSION = "Current session Starts with your first message 0% used"
PAUSED_SESSION = "Current session Paused until your week resets 8% used"
IDLE_FABLE = "Fable You haven’t used Fable yet 0% used"
LIVE_WEEKLY = "All models Resets Mon 5:59 PM 2% used"
NEW_SESSION = "Current session Resets at 11:00 PM 12% used"
NEW_WEEKLY = "This week Resets Monday 6:00 PM 77% used"
NEW_FABLE = (
    "Fable this week Separate weekly limit for Fable · "
    "Resets Monday 6:00 PM 63% used"
)
# Real banner text from the Max-plan usage dialog. It names Fable but is not a
# usage row, and it sits directly above the bars.
BANNER = (
    "Fable 5 is still included with your Max plan. If you see a prompt to "
    "set up usage credits for it, restart Claude Code."
)


def run_extractor(tmp_path, rows: list[tuple[str, int]]) -> dict:
    """Run EXTRACTOR_JS against a mock DOM. rows are (text, height) pairs.

    The first row stands in for the page wrapper and supplies document.body
    text, matching how the real page nests every row inside it.
    """
    harness = tmp_path / "harness.js"
    script = tmp_path / "extractor.js"
    nodes = tmp_path / "nodes.json"
    harness.write_text(HARNESS, encoding="utf-8")
    script.write_text(EXTRACTOR_JS, encoding="utf-8")
    nodes.write_text(json.dumps(rows), encoding="utf-8")
    out = subprocess.run(
        ["node", str(harness), str(nodes), str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


def percents(result: dict) -> dict:
    return {
        key: (result[key] or {}).get("percent")
        for key in ("session", "weekly_all", "weekly_fable")
    }


def test_login_route_without_login_link_reports_signed_out(tmp_path):
    """Claude's /new → /logout → /login redirect must not loop back to /new."""
    harness = tmp_path / "harness.js"
    script = tmp_path / "extractor.js"
    nodes = tmp_path / "nodes.json"
    harness.write_text(
        HARNESS.replace("https://claude.ai/settings/usage", "https://claude.ai/login")
        .replace("pathname: '/settings/usage'", "pathname: '/login'"),
        encoding="utf-8",
    )
    script.write_text(EXTRACTOR_JS, encoding="utf-8")
    nodes.write_text(json.dumps([("Welcome back Sign in with Google", 900)]), encoding="utf-8")
    result = json.loads(subprocess.check_output(
        ["node", str(harness), str(nodes), str(script)],
        text=True,
        encoding="utf-8",
    ))
    assert result["logged_out"] is True
    assert "__retry_after_ms" not in result
    assert result["url"] == "https://claude.ai/login"


def test_sign_in_page_on_new_route_reports_signed_out(tmp_path):
    harness = tmp_path / "harness.js"
    script = tmp_path / "extractor.js"
    nodes = tmp_path / "nodes.json"
    harness.write_text(
        HARNESS.replace("title: 'Claude'", "title: 'Sign in - Claude'"),
        encoding="utf-8",
    )
    script.write_text(EXTRACTOR_JS, encoding="utf-8")
    nodes.write_text(json.dumps([("Welcome back Sign in with Google", 900)]), encoding="utf-8")
    result = json.loads(subprocess.check_output(
        ["node", str(harness), str(nodes), str(script)],
        text=True,
        encoding="utf-8",
    ))
    assert result["logged_out"] is True
    assert "__retry_after_ms" not in result


def test_max_plan_layout_reads_all_three_rows(tmp_path):
    wrapper = " ".join(
        ["Plan usage limits Max (5x)", SESSION, "Weekly limits", BANNER, ALL_MODELS, FABLE]
    )
    result = run_extractor(
        tmp_path,
        [
            (wrapper, 900),
            (SESSION, 60),
            (" ".join([BANNER, ALL_MODELS, FABLE]), 400),
            (BANNER, 90),
            (ALL_MODELS, 60),
            (FABLE, 60),
        ],
    )

    assert percents(result) == {"session": 18, "weekly_all": 2, "weekly_fable": 4}
    assert result["weekly_fable"]["reset_text"] == "22 hr 0 min"


def test_renamed_september_2026_usage_rows(tmp_path):
    """Claude's new dialog calls the weekly rows This week/Fable this week."""
    wrapper = " ".join(
        [
            "Your usage Max (5x)",
            "Heads up. At this pace you’ll run out tomorrow evening.",
            NEW_SESSION,
            NEW_WEEKLY,
            NEW_FABLE,
            "Usage credits",
        ]
    )
    result = run_extractor(
        tmp_path,
        [
            (wrapper, 1100),
            (NEW_SESSION, 60),
            (NEW_WEEKLY, 60),
            (NEW_FABLE, 90),
        ],
    )

    assert percents(result) == {
        "session": 12,
        "weekly_all": 77,
        "weekly_fable": 63,
    }
    assert result["session"]["reset_text"] == "at 11:00 PM"
    assert result["weekly_all"]["reset_text"] == "Monday 6:00 PM"
    assert result["weekly_fable"]["reset_text"] == "Monday 6:00 PM"
    assert "__retry_after_ms" not in result


def test_fable_banner_without_a_fable_row_is_not_read_as_a_row(tmp_path):
    """The banner names Fable but carries no usage of its own.

    A container holding the banner plus the All-models row must not be read as
    the Fable row — that would report the neighbouring row's percentage.
    """
    wrapper = " ".join(["Plan usage limits", SESSION, "Weekly limits", BANNER, ALL_MODELS])
    result = run_extractor(
        tmp_path,
        [
            (wrapper, 900),
            (SESSION, 60),
            (" ".join([BANNER, ALL_MODELS]), 300),
            (BANNER, 90),
            (ALL_MODELS, 60),
        ],
    )

    assert percents(result) == {"session": 18, "weekly_all": 2, "weekly_fable": None}


def test_plan_without_fable_row_still_reads_session_and_weekly(tmp_path):
    wrapper = " ".join(["Plan usage limits", SESSION, ALL_MODELS])
    result = run_extractor(
        tmp_path, [(wrapper, 900), (SESSION, 60), (ALL_MODELS, 60)]
    )

    assert percents(result) == {"session": 18, "weekly_all": 2, "weekly_fable": None}


def test_fable_row_is_found_when_only_nested_beside_the_banner(tmp_path):
    """No tight row element — the banner and the row share one container."""
    wrapper = " ".join(["Plan usage limits", SESSION, ALL_MODELS, BANNER, FABLE])
    result = run_extractor(
        tmp_path,
        [
            (wrapper, 900),
            (SESSION, 60),
            (ALL_MODELS, 60),
            (" ".join([BANNER, FABLE]), 130),
        ],
    )

    assert percents(result)["weekly_fable"] == 4


def test_versioned_fable_row_label_still_matches(tmp_path):
    versioned = "Fable 5 Resets in 22 hr 0 min 7% used"
    wrapper = " ".join(["Plan usage limits", SESSION, ALL_MODELS, versioned])
    result = run_extractor(
        tmp_path,
        [(wrapper, 900), (SESSION, 60), (ALL_MODELS, 60), (versioned, 60)],
    )

    assert percents(result)["weekly_fable"] == 7


def test_current_claude_idle_session_layout_reports_mixed_usage(tmp_path):
    wrapper = " ".join(
        [
            "Plan usage limits Max (5x)",
            IDLE_SESSION,
            "Weekly limits",
            BANNER,
            LIVE_WEEKLY,
            IDLE_FABLE,
        ]
    )
    result = run_extractor(
        tmp_path,
        [
            (wrapper, 1100),
            (IDLE_SESSION, 80),
            (" ".join([BANNER, LIVE_WEEKLY, IDLE_FABLE]), 400),
            (BANNER, 90),
            (LIVE_WEEKLY, 60),
            (IDLE_FABLE, 80),
        ],
    )

    assert percents(result) == {"session": 0, "weekly_all": 2, "weekly_fable": 0}
    assert result["session"]["reset_text"] is None
    assert result["weekly_all"]["reset_text"] == "Mon 5:59 PM"
    assert result["weekly_fable"]["reset_text"] is None
    assert "__retry_after_ms" not in result


def test_september_2026_idle_session_copy_is_ready_and_reports_zero(tmp_path):
    """The idle session row has no reset time but is fully hydrated."""
    wrapper = " ".join(
        [
            "Your usage Max (5x)",
            NEW_IDLE_SESSION,
            NEW_WEEKLY,
            NEW_FABLE,
            "Usage credits",
        ]
    )
    result = run_extractor(
        tmp_path,
        [
            (wrapper, 1100),
            (NEW_IDLE_SESSION, 80),
            (NEW_WEEKLY, 60),
            (NEW_FABLE, 90),
        ],
    )

    assert percents(result) == {
        "session": 0,
        "weekly_all": 77,
        "weekly_fable": 63,
    }
    assert result["session"]["reset_text"] is None
    assert result["session"]["kind"] == "used"
    assert "__retry_after_ms" not in result


def test_weekly_cap_pauses_session_without_making_the_dialog_look_incomplete(tmp_path):
    """A capped account keeps its Session percentage but replaces its reset copy."""
    capped_weekly = "This week Resets at 6:00 PM 100% used"
    live_fable = (
        "Fable this week Separate weekly limit for Fable · "
        "Resets at 6:00 PM 76% used"
    )
    wrapper = " ".join(
        [
            "Your usage Max (5x)",
            "Limit hit. You can send new messages when your week resets.",
            PAUSED_SESSION,
            capped_weekly,
            live_fable,
            "Usage credits",
        ]
    )

    result = run_extractor(
        tmp_path,
        [(wrapper, 1100), (PAUSED_SESSION, 80), (capped_weekly, 60), (live_fable, 90)],
    )

    assert percents(result) == {
        "session": 8,
        "weekly_all": 100,
        "weekly_fable": 76,
    }
    assert result["session"]["reset_text"] == "Paused until your week resets"
    assert "__retry_after_ms" not in result


def test_status_wording_can_change_when_rows_only_share_a_wrapper(tmp_path):
    """Labels and values, rather than exact prose, define a hydrated row."""
    session = "Current session Temporarily unavailable until allowance renewal 8% used"
    weekly = "This week Your plan renews Monday evening 100% used"

    result = run_extractor(tmp_path, [(f"Your usage {session} {weekly}", 400)])

    assert percents(result) == {
        "session": 8,
        "weekly_all": 100,
        "weekly_fable": None,
    }
    assert "__retry_after_ms" not in result


def test_nested_label_only_elements_do_not_beat_rows_with_values(tmp_path):
    """Claude renders headings separately inside the complete usage rows."""
    capped_weekly = "This week Resets at 6:00 PM 100% used"
    live_fable = (
        "Fable this week Separate weekly limit for Fable · "
        "Resets at 6:00 PM 76% used"
    )
    wrapper = " ".join(["Your usage", PAUSED_SESSION, capped_weekly, live_fable])

    result = run_extractor(
        tmp_path,
        [
            (wrapper, 900),
            ("Current session", 20),
            (PAUSED_SESSION, 80),
            ("This week", 20),
            (capped_weekly, 60),
            ("Fable this week", 20),
            (live_fable, 90),
        ],
    )

    assert percents(result) == {
        "session": 8,
        "weekly_all": 100,
        "weekly_fable": 76,
    }
    assert "__retry_after_ms" not in result


def test_idle_copy_overrides_neighbor_percentage_in_shared_container(tmp_path):
    shared = " ".join([IDLE_SESSION, LIVE_WEEKLY])

    result = run_extractor(tmp_path, [(shared, 100)])

    assert percents(result) == {
        "session": 0,
        "weekly_all": 2,
        "weekly_fable": None,
    }


def test_legacy_idle_panel_without_percentages_reports_zero_rows(tmp_path):
    idle = (
        "Plan usage limits Current session Resets when you next use this limit "
        "All models Resets when you next use this limit"
    )
    result = run_extractor(tmp_path, [(idle, 400)])

    assert percents(result) == {
        "session": 0,
        "weekly_all": 0,
        "weekly_fable": None,
    }


def test_legacy_new_page_redirects_to_direct_usage_route(tmp_path):
    harness = tmp_path / "harness.js"
    script = tmp_path / "extractor.js"
    nodes = tmp_path / "nodes.json"
    harness.write_text(
        HARNESS.replace("https://claude.ai/settings/usage", "https://claude.ai/new")
        .replace("pathname: '/settings/usage'", "pathname: '/new'"),
        encoding="utf-8",
    )
    script.write_text(EXTRACTOR_JS, encoding="utf-8")
    nodes.write_text(json.dumps([("New chat Projects Recents This week 80% used", 900)]), encoding="utf-8")
    result = json.loads(subprocess.check_output(
        ["node", str(harness), str(nodes), str(script)],
        text=True,
        encoding="utf-8",
    ))
    assert result["__retry_reason"] == "opened usage page"
    assert result["url"] == "/settings/usage"


def test_november_2026_ultra_empty_usage_without_session_row(tmp_path):
    """Claude displays weekly 0% without any Current session row at idle."""
    weekly = "This week Resets Sunday 6:00 AM 0% used"
    empty = "No usage yet. 100% of your weekly limit is available."
    wrapper = " ".join([
        "Your usage Ultra", empty, weekly,
        "Limit resets No resets right now",
    ])
    result = run_extractor(tmp_path, [(wrapper, 800), (weekly, 75)])
    assert "__retry_after_ms" not in result
    assert percents(result) == {
        "session": 0, "weekly_all": 0, "weekly_fable": None,
    }
    assert result["session"]["kind"] == "used"
    assert result["session"]["reset_text"] is None
    assert result["weekly_all"]["reset_text"] == "Sunday 6:00 AM"


def test_ultra_empty_message_does_not_mask_missing_weekly_row(tmp_path):
    """Do not return a false success for a partly rendered usage panel."""
    text = "Your usage Ultra No usage yet. 100% of your weekly limit is available."
    result = run_extractor(tmp_path, [(text, 800)])
    assert result["__retry_reason"] == "usage dialog not ready"


def test_ultra_empty_message_does_not_override_nonzero_weekly_usage(tmp_path):
    """Only an actual 0% weekly row is enough to infer a zero session."""
    weekly = "This week Resets Sunday 6:00 AM 21% used"
    text = "Your usage Ultra No usage yet. 100% of your weekly limit is available. " + weekly
    result = run_extractor(tmp_path, [(text, 800), (weekly, 75)])
    assert result["__retry_reason"] == "usage dialog not ready"



def test_ultra_empty_usage_with_adjacent_dom_text_nodes(tmp_path):
    """Real Claude textContent joins h2/span and button/span without spaces."""
    # Reconstructed from the 9 November 2026 Ultra usage-page HTML.
    # Key markup: <h2>Your usage</h2><span>Ultra</span>,
    #            <button>This week<span>icon</span></button>,
    #            <span>Resets Sunday 6:00 AM</span><span>0% used</span>.
    weekly = "This week\ue027Resets Sunday 6:00 AM0% used"
    body = (
        "Your usageUltra"
        "No usage yet. 100% of your weekly limit is available."
        + weekly
        + "Limit resetsNo resets right now"
        + "Usage creditsBuy usage credits Up to 30% off"
    )
    result = run_extractor(tmp_path, [(body, 900), (weekly, 65)])
    assert "__retry_after_ms" not in result
    assert percents(result) == {
        "session": 0,
        "weekly_all": 0,
        "weekly_fable": None,
    }
    assert result["weekly_all"]["reset_text"] == "Sunday 6:00 AM"



def test_ultra_empty_usage_semantic_marker(tmp_path):
    """Detect the usage root even when textContent merges heading nodes."""
    harness = tmp_path / "harness.js"
    script = tmp_path / "extractor.js"
    nodes = tmp_path / "nodes.json"
    marker_query = "querySelector: (sel) => (sel === '[data-usage-bars=\"usage_page\"]' ? {} : null),"
    harness_text = HARNESS.replace("querySelector: () => null,", marker_query)
    assert harness_text != HARNESS
    harness.write_text(harness_text, encoding="utf-8")
    script.write_text(EXTRACTOR_JS, encoding="utf-8")
    weekly = "This week\ue027Resets Sunday 6:00 AM0% used"
    body = "UltraNo usage yet. 100% of your weekly limit is available." + weekly
    nodes.write_text(json.dumps([(body, 900), (weekly, 75)]), encoding="utf-8")
    result = json.loads(subprocess.check_output(
        ["node", str(harness), str(nodes), str(script)],
        text=True,
        encoding="utf-8",
    ))
    assert "__retry_after_ms" not in result
    assert result["session"]["percent"] == 0
    assert result["weekly_all"]["percent"] == 0


# Live Max-account page, 2026-11-11: the Fable row hides inside a collapsed
# "This week" disclosure, and the credit card below it mentions Fable.
LIVE_SESSION = "Current sessionResets at 2:30 PM6% used"
LIVE_WEEK = "This weekResets Monday 5:00 PM25% used"
LIVE_FABLE_ROW = "FableUp to 50% of your weekly limitResets Monday 5:00 PM22% used"
LIVE_HEAD = (
    "Your usageMaxOn track. You should reach Monday’s reset with room to spare."
)
LIVE_TAIL = (
    "Limit resets4 availableFull resetExpires Dec 10, 5:00 PM ESTReset for free"
    "Fable reset · 3All expire Mar 9, 2027, 2:59 AM ESTRefills your 5-hour limit "
    "and your Fable allowance, and gives back what Fable used on your weekly "
    "limit. It doesn’t reset your whole week.Reset for freeUsage credits"
    "Available for any task. Promotional credits are used before purchased "
    "credits.$0Turn on usage credits to keep using Claude if you hit a plan "
    "limit. Learn more(opens in new tab)Buy usage credits · Up to 30% off"
    "Monthly spend limit$0.00 of $50 this month · Auto-reload off"
)
COLLAPSED_BODY = LIVE_HEAD + LIVE_SESSION + LIVE_WEEK + LIVE_TAIL
EXPANDED_BODY = LIVE_HEAD + LIVE_SESSION + LIVE_WEEK + LIVE_FABLE_ROW + LIVE_TAIL

TOGGLE_HARNESS = (
    HARNESS.replace(
        "querySelector: () => null,",
        "querySelector: (sel) => (sel === 'button[data-testid=\"pace-week-toggle\"]' "
        "&& process.argv[4] ? toggle : null),",
    )
    .replace(
        "global.document = {",
        "const clicks = [];\n"
        "const toggle = {\n"
        "  textContent: 'This week',\n"
        "  getAttribute: (name) => (name === 'aria-expanded' ? process.argv[4] : null),\n"
        "  click: () => clicks.push('click'),\n"
        "};\n"
        "global.document = {",
    )
    .replace(
        "const result = eval(fs.readFileSync(process.argv[3], 'utf8'));\n"
        "process.stdout.write(JSON.stringify(result));",
        "const src = fs.readFileSync(process.argv[3], 'utf8');\n"
        "const results = [];\n"
        "for (let i = 0; i < Number(process.argv[5]); i++) results.push(eval(src));\n"
        "process.stdout.write(JSON.stringify({ results, clicks: clicks.length }));",
    )
)
assert TOGGLE_HARNESS.count("process.argv[5]") == 1


def run_with_toggle(tmp_path, rows, state: str, runs: int = 1) -> dict:
    """Run the extractor `runs` times in one Node process, like the scraper's
    reruns in one document. state is the toggle's aria-expanded ('' = no toggle).
    """
    harness = tmp_path / "toggle_harness.js"
    script = tmp_path / "extractor.js"
    nodes = tmp_path / "nodes.json"
    harness.write_text(TOGGLE_HARNESS, encoding="utf-8")
    script.write_text(EXTRACTOR_JS, encoding="utf-8")
    nodes.write_text(json.dumps(rows), encoding="utf-8")
    out = subprocess.run(
        ["node", str(harness), str(nodes), str(script), state, str(runs)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


def test_collapsed_weekly_breakdown_without_toggle_has_no_fable(tmp_path):
    """The credit card's Fable mention plus "Up to 30% off" is not a row."""
    result = run_extractor(
        tmp_path,
        [(COLLAPSED_BODY, 900), (LIVE_SESSION, 60), (LIVE_WEEK, 60)],
    )

    assert percents(result) == {"session": 6, "weekly_all": 25, "weekly_fable": None}
    assert "__retry_after_ms" not in result


def test_collapsed_weekly_breakdown_is_expanded_once_and_retried(tmp_path):
    out = run_with_toggle(
        tmp_path,
        [(COLLAPSED_BODY, 900), (LIVE_SESSION, 60), (LIVE_WEEK, 60)],
        "false",
    )

    (result,) = out["results"]
    assert result["__retry_reason"] == "expanded weekly breakdown"
    assert result["__retry_after_ms"] == 600
    assert out["clicks"] == 1


def test_expanded_weekly_breakdown_reads_the_fable_row(tmp_path):
    out = run_with_toggle(
        tmp_path,
        [
            (EXPANDED_BODY, 900),
            (LIVE_SESSION, 60),
            (LIVE_WEEK, 60),
            (LIVE_FABLE_ROW, 60),
        ],
        "true",
    )

    (result,) = out["results"]
    assert percents(result) == {"session": 6, "weekly_all": 25, "weekly_fable": 22}
    assert result["weekly_fable"]["kind"] == "used"
    assert result["weekly_fable"]["reset_text"] == "Monday 5:00 PM"
    assert "__retry_after_ms" not in result
    assert out["clicks"] == 0


def test_weekly_toggle_is_clicked_only_once_per_document(tmp_path):
    """A second click would collapse the section again."""
    out = run_with_toggle(
        tmp_path,
        [(COLLAPSED_BODY, 900), (LIVE_SESSION, 60), (LIVE_WEEK, 60)],
        "false",
        runs=2,
    )

    first, second = out["results"]
    assert first["__retry_reason"] == "expanded weekly breakdown"
    assert "__retry_after_ms" not in second
    assert second["weekly_fable"] is None
    assert percents(second)["weekly_all"] == 25
    assert out["clicks"] == 1


def test_bare_fable_label_needs_a_usage_reading_not_a_promo_percentage(tmp_path):
    promo = "Fable reset · 3All expire Mar 9, 2027 Buy usage credits · Up to 30% off"
    wrapper = " ".join(["Plan usage limits", SESSION, ALL_MODELS, promo])
    result = run_extractor(
        tmp_path,
        [(wrapper, 900), (SESSION, 60), (ALL_MODELS, 60), (promo, 90)],
    )
    assert percents(result)["weekly_fable"] is None

    reading = "Fable Up to 50% of your weekly limit Resets Monday 5:00 PM 22% used"
    result = run_extractor(
        tmp_path,
        [(" ".join([wrapper, reading]), 900), (SESSION, 60), (ALL_MODELS, 60), (reading, 60)],
    )
    assert percents(result)["weekly_fable"] == 22
