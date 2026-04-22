import csv
import getpass
import html as html_lib
import json
import re
import sys
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://www.gradescope.com"
EMAIL_RE = re.compile(r'[\w.\-+]+@[\w.\-]+\.\w+')


def normalize_space(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def first_email(text):
    m = EMAIL_RE.search(text or "")
    return m.group(0) if m else ""


def parse_name_from_row_text(row_text):
    text = row_text or ""
    email = first_email(text)
    if email:
        text = text.replace(email, " ")
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    return lines[0] if lines else ""


def iso_sort_key(ts):
    if not ts:
        return datetime.max
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return datetime.max


def login(page, email, password):
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")

    page.fill('input[name="session[email]"]', email)
    page.fill('input[name="session[password]"]', password)

    for sel in [
        'input[type="submit"]',
        'button[type="submit"]',
        'input[name="commit"]',
        'button:has-text("Log In")',
        'button:has-text("Sign In")',
    ]:
        loc = page.locator(sel)
        if loc.count() > 0:
            try:
                loc.first.click()
                break
            except Exception:
                pass
    else:
        raise RuntimeError("Could not find login submit button.")

    page.wait_for_load_state("networkidle")

    if "/login" in page.url:
        raise RuntimeError("Login failed; still on login page.")

    print("Logged in.")


def extract_submission_rows_from_current_page(page, course_id, assignment_id):
    return page.evaluate(
        """
        ({courseId, assignmentId}) => {
          const re = new RegExp(`^/courses/${courseId}/assignments/${assignmentId}/submissions/(\\\\d+)$`);
          const out = [];
          const seen = new Set();

          const anchors = document.querySelectorAll('#DataTables_Table_0 tbody a[href]');

          for (const a of anchors) {
            const u = new URL(a.getAttribute('href'), location.origin);
            const m = u.pathname.match(re);
            if (!m) continue;

            const abs = u.origin + u.pathname;
            if (seen.has(abs)) continue;
            seen.add(abs);

            const row =
              a.closest('tr') ||
              a.closest('[role="row"]') ||
              a.parentElement;

            out.push({
              submission_url: abs,
              submission_id: m[1],
              row_text: (row?.innerText || a.innerText || '').trim()
            });
          }

          const currentPage =
            document.querySelector('a.paginate_button.current')?.textContent?.trim() || "";

          const nextDisabled = (() => {
            const btn = document.querySelector('#DataTables_Table_0_next');
            if (!btn) return true;
            const cls = btn.getAttribute('class') || "";
            const aria = btn.getAttribute('aria-disabled') || "false";
            return cls.includes('disabled') || aria === 'true';
          })();

          return { out, currentPage, nextDisabled };
        }
        """,
        {"courseId": str(course_id), "assignmentId": str(assignment_id)},
    )


def get_submission_page_rows(page, course_id, assignment_id, max_pages=500):
    url = f"{BASE_URL}/courses/{course_id}/assignments/{assignment_id}/submissions"
    page.goto(url, wait_until="networkidle")

    seen_submission_urls = set()
    visited_page_labels = set()
    rows = []

    for _ in range(max_pages):
        page.wait_for_selector("#DataTables_Table_0 tbody tr")

        payload = extract_submission_rows_from_current_page(page, course_id, assignment_id)
        page_rows = payload["out"]
        current_page_label = payload["currentPage"] or "1"
        next_disabled = payload["nextDisabled"]

        if current_page_label in visited_page_labels:
            break
        visited_page_labels.add(current_page_label)

        new_count = 0
        for item in page_rows:
            sub_url = item["submission_url"]
            if sub_url not in seen_submission_urls:
                seen_submission_urls.add(sub_url)
                row_text = item.get("row_text", "")
                rows.append({
                    "current_submission_id": item["submission_id"],
                    "current_submission_url": sub_url,
                    "student_email": first_email(row_text),
                    "student_name": parse_name_from_row_text(row_text),
                    "row_text": normalize_space(row_text),
                })
                new_count += 1

        print(f"Submissions page {current_page_label}: {len(page_rows)} links, {new_count} new")

        if next_disabled:
            break

        first_href_before = page_rows[0]["submission_url"] if page_rows else ""

        page.evaluate(
            """
            () => {
              const btn = document.querySelector('#DataTables_Table_0_next');
              if (btn) btn.click();
            }
            """
        )

        page.wait_for_function(
            """
            ({prevPage, prevFirst}) => {
              const currentPage =
                document.querySelector('a.paginate_button.current')?.textContent?.trim() || "";

              const firstAnchor = document.querySelector('#DataTables_Table_0 tbody a[href]');
              const firstHref = firstAnchor
                ? new URL(firstAnchor.getAttribute('href'), location.origin).href
                : "";

              return currentPage !== prevPage || firstHref !== prevFirst;
            }
            """,
            arg={"prevPage": current_page_label, "prevFirst": first_href_before},
            timeout=10000,
        )

        page.wait_for_timeout(500)

    print(f"Found {len(rows)} current submission pages total.")
    return rows


def extract_props_from_page(page):
    loc = page.locator('[data-react-class="AssignmentSubmissionViewer"]')
    if loc.count() == 0:
        raise RuntimeError("Could not find AssignmentSubmissionViewer on page.")

    raw = loc.first.get_attribute("data-react-props")
    if not raw:
        raise RuntimeError("Missing data-react-props.")

    try:
        return json.loads(html_lib.unescape(raw))
    except Exception as e:
        raise RuntimeError(f"Could not parse data-react-props JSON: {e}")


def extract_runtime_ms(props):
    autograder = props.get("autograder_results") or {}
    tests = autograder.get("tests") or []

    for test in tests:
        name = (test.get("name") or "").strip().lower()
        output = str(test.get("output") or "")
        if "test case timing" in name:
            m = re.search(r'([-+]?\d+(?:\.\d+)?)\s*ms', output, re.I)
            if m:
                return m.group(1)

    leaderboard = autograder.get("leaderboard") or []
    numeric_vals = []
    for item in leaderboard:
        try:
            numeric_vals.append(float(item.get("value")))
        except Exception:
            pass
    if numeric_vals:
        return str(min(numeric_vals))

    return ""


def extract_student_name_from_submission_page(page, fallback=""):
    candidates = [
        'label[for="submission_owner_id"] + p',
        'form .form--group p',
    ]
    for sel in candidates:
        loc = page.locator(sel)
        if loc.count() > 0:
            try:
                txt = normalize_space(loc.first.inner_text())
                if txt and txt.lower() != "ta":
                    return txt
            except Exception:
                pass
    return fallback


def extract_submission_core(page):
    props = extract_props_from_page(page)

    assignment_submission = props.get("assignment_submission") or {}

    submission_id = str(assignment_submission.get("id", ""))
    submission_time = assignment_submission.get("created_at", "") or ""
    active = assignment_submission.get("active", "")
    score = assignment_submission.get("score", "")
    runtime_ms = extract_runtime_ms(props)

    return {
        "submission_id": submission_id,
        "submission_time": submission_time,
        "active": active,
        "score": score,
        "runtime_ms": runtime_ms,
        "props": props,
    }


def collect_exact_submission_links(page, course_id, assignment_id):
    links = page.evaluate(
        """
        ({courseId, assignmentId}) => {
          const re = new RegExp(`^/courses/${courseId}/assignments/${assignmentId}/submissions/(\\\\d+)$`);
          return [...document.querySelectorAll('a[href]')]
            .map(a => {
              const u = new URL(a.getAttribute('href'), location.origin);
              return re.test(u.pathname) ? (u.origin + u.pathname) : null;
            })
            .filter(Boolean);
        }
        """,
        {"courseId": str(course_id), "assignmentId": str(assignment_id)},
    )
    return set(links)


def click_submission_history(page):
    candidates = [
        page.get_by_role("button", name="Submission History"),
        page.get_by_role("link", name="Submission History"),
        page.locator('button:has-text("Submission History")'),
        page.locator('a:has-text("Submission History")'),
        page.locator('text="Submission History"'),
    ]

    for loc in candidates:
        count = min(loc.count(), 5)
        for i in range(count):
            item = loc.nth(i)
            try:
                if item.is_visible():
                    item.click()
                    page.wait_for_timeout(1200)
                    return True
            except Exception:
                pass
    return False


def collect_history_submission_urls(page, course_id, assignment_id, current_url, current_submission_id):
    before_links = collect_exact_submission_links(page, course_id, assignment_id)
    clicked = click_submission_history(page)

    if not clicked:
        with open(f"debug_history_{current_submission_id}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        return [current_url]

    page.wait_for_timeout(800)
    after_links = collect_exact_submission_links(page, course_id, assignment_id)

    new_urls = sorted(after_links - before_links)

    if new_urls:
        urls = sorted(set(new_urls + [current_url]))
    else:
        urls = sorted(set(after_links | {current_url}))

    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    except Exception:
        pass

    if len(urls) == 1:
        with open(f"debug_history_{current_submission_id}.html", "w", encoding="utf-8") as f:
            f.write(page.content())

    return urls


def dedupe_records(records):
    seen = set()
    out = []

    for r in records:
        key = (
            r.get("current_submission_id", ""),
            r.get("submission_id", ""),
            r.get("submission_time", ""),
            str(r.get("active", "")),
            r.get("runtime_ms", ""),
        )
        if key not in seen:
            seen.add(key)
            out.append(r)

    return out


def export_all(email, password, course_id, assignment_id, out_csv, headless=True):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        login(page, email, password)
        current_rows = get_submission_page_rows(page, course_id, assignment_id)

        all_rows = []

        for idx, row in enumerate(current_rows, 1):
            current_url = row["current_submission_url"]
            current_submission_id = row["current_submission_id"]

            print(f"[{idx}/{len(current_rows)}] Processing {current_submission_id}")
            page.goto(current_url, wait_until="networkidle")

            student_name = extract_student_name_from_submission_page(page, row["student_name"])

            history_urls = collect_history_submission_urls(
                page, course_id, assignment_id, current_url, current_submission_id
            )

            version_rows = []

            for hist_url in history_urls:
                try:
                    page.goto(hist_url, wait_until="networkidle")
                    core = extract_submission_core(page)

                    version_rows.append({
                        "student_name": student_name,
                        "student_email": row["student_email"],
                        "current_submission_id": current_submission_id,
                        "submission_id": core["submission_id"],
                        "submission_time": core["submission_time"],
                        "runtime_ms": core["runtime_ms"],
                        "score": core["score"],
                        "active": core["active"],
                        "current_submission_url": current_url,
                        "submission_url": hist_url,
                    })
                except Exception as e:
                    version_rows.append({
                        "student_name": student_name,
                        "student_email": row["student_email"],
                        "current_submission_id": current_submission_id,
                        "submission_id": "",
                        "submission_time": "",
                        "runtime_ms": "",
                        "score": "",
                        "active": "",
                        "current_submission_url": current_url,
                        "submission_url": hist_url,
                        "error": str(e),
                    })

            version_rows = dedupe_records(version_rows)
            version_rows.sort(key=lambda r: iso_sort_key(r.get("submission_time", "")))

            for attempt_num, rec in enumerate(version_rows, 1):
                rec["attempt_number"] = attempt_num
                all_rows.append(rec)

        fieldnames = [
            "student_name",
            "student_email",
            "current_submission_id",
            "submission_id",
            "attempt_number",
            "submission_time",
            "runtime_ms",
            "score",
            "active",
            "current_submission_url",
            "submission_url",
            "error",
        ]

        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)

        browser.close()
        print(f"Wrote {len(all_rows)} rows to {out_csv}")


if __name__ == "__main__":
    if len(sys.argv) not in (5, 6):
        print("Usage:")
        print("  python gs_export.py <email> <course_id> <assignment_id> <out.csv>")
        print("  python gs_export.py <email> <password> <course_id> <assignment_id> <out.csv>")
        sys.exit(1)

    if len(sys.argv) == 5:
        email, course_id, assignment_id, out_csv = sys.argv[1:]
        password = getpass.getpass("Gradescope password: ")
    else:
        email, password, course_id, assignment_id, out_csv = sys.argv[1:]

    export_all(email, password, course_id, assignment_id, out_csv, headless=True)
    