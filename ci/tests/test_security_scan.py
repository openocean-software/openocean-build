"""Tests ci/security-scan without the network: OSV, NVD, Scorecard and GitHub's
issues API are replaced by a fake that answers from canned responses and records
each request, and git ls-remote by a dictionary of refs.

Run from the repository root:
  python3 -m unittest discover -s ci/tests
"""

import importlib.machinery
import importlib.util
import json
import subprocess
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

_loader = importlib.machinery.SourceFileLoader("security_scan", str(ROOT / "ci" / "security-scan"))
_spec = importlib.util.spec_from_loader("security_scan", _loader)
scan = importlib.util.module_from_spec(_spec)
_loader.exec_module(scan)

DEFAULTS = yaml.safe_load((ROOT / "defaults.yaml").read_text())
SHA_TAG = "a" * 40
SHA_PIN = "b" * 40
SHA_SUB = "c" * 40
SHA_HEAD = "d" * 40


def config(**override):
    return scan.project_config.merge(DEFAULTS, override)


class FakeApi:
    """Answers (method, url) with canned (status, body), by exact URL or else by URL prefix;
    records every request."""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def __call__(self, method, url, body=None, token=None, headers=None):
        self.requests.append((method, url, body))
        if (method, url) in self.responses:
            response = self.responses[(method, url)]
            return response(body) if callable(response) else response
        for (m, prefix), response in self.responses.items():
            if m == method and url.startswith(prefix):
                return response(body) if callable(response) else response
        raise AssertionError(f"unexpected request {method} {url}")


def resolve(refs):
    def lookup(url, ref):
        return refs[(url, ref)]
    return lookup


class FakeResponse:
    def __init__(self, body):
        self.status = 200
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self.body).encode()


def http_error(code):
    return urllib.error.HTTPError("https://example.org", code, "error", {}, None)


class HttpRetryTest(unittest.TestCase):
    """http() retries what NVD's API does when it's struggling (503s, timeouts),
    sleeping with backoff between attempts, but not errors that won't go away."""

    def run_http(self, outcomes, method="GET", url=scan.NVD_API):
        """Each outcome is an exception to raise, or a body to answer with."""
        calls, sleeps = [], []

        def urlopen(request, timeout):
            calls.append(request.full_url)
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return FakeResponse(outcome)

        result = scan.http(method, url, urlopen=urlopen, sleep=sleeps.append)
        return result, calls, sleeps

    def test_server_errors_and_timeouts_are_retried_with_backoff(self):
        result, calls, sleeps = self.run_http([http_error(503), TimeoutError("timed out"), {"ok": True}])
        self.assertEqual(result, (200, {"ok": True}))
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [5, 10])

    def test_rate_limit_and_dropped_connection_are_retried(self):
        result, calls, _ = self.run_http([http_error(429), urllib.error.URLError("reset"), {"ok": True}])
        self.assertEqual(result, (200, {"ok": True}))
        self.assertEqual(len(calls), 3)

    def test_gives_up_after_the_last_attempt(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.run_http([http_error(503)] * 4)

    def test_other_client_errors_are_not_retried(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.run_http([http_error(403), {"ok": True}])

    def test_not_found_is_a_result(self):
        result, calls, _ = self.run_http([http_error(404)])
        self.assertEqual(result, (404, None))
        self.assertEqual(len(calls), 1)

    def test_a_github_post_is_not_retried(self):
        # A POST that failed may still have created the issue
        with self.assertRaises(urllib.error.HTTPError):
            self.run_http([http_error(502), {"ok": True}], method="POST",
                          url=f"{scan.GITHUB_API}/repos/o/r/issues")


class SourcesTest(unittest.TestCase):
    """Which upstream source a build takes, and so what gets scanned."""

    def test_a_tagged_release_resolves_its_tag(self):
        c = config(upstream={"git": "https://github.com/o/p.git", "tag_format": "v{version}"}, source="git")
        found = scan.sources("p", c, version="1.2.0~rc.1",
                             resolve=resolve({("https://github.com/o/p.git", "refs/tags/v1.2.0-rc.1"): SHA_TAG}))
        self.assertEqual(found, [{"name": "p", "url": "https://github.com/o/p.git", "ref": "v1.2.0-rc.1",
                                  "commit": SHA_TAG, "cpe_version": "1.2.0~rc.1"}])

    def test_a_pinned_release_is_its_commit_and_an_untagged_one_has_no_version(self):
        c = config(upstream={"git": "https://github.com/o/p.git", "tag_format": None}, source="git",
                   release={"commit": SHA_PIN})
        found = scan.sources("p", c, version="2.0.0+git20260507.bbbbbbb", resolve=resolve({}))
        self.assertEqual(found[0]["commit"], SHA_PIN)
        self.assertEqual((found[0]["ref"], found[0]["cpe_version"]), ("", None))

    def test_a_snapshot_project_scans_its_branch_head(self):
        c = config(upstream={"git": "https://github.com/o/p.git"}, source="git",
                   release={"enabled": False}, continuous={"enabled": True, "branch": "dev"})
        found = scan.sources("p", c, resolve=resolve({("https://github.com/o/p.git", "refs/heads/dev"): SHA_HEAD}))
        self.assertEqual((found[0]["ref"], found[0]["commit"]), ("", SHA_HEAD))

    def test_submodules_are_scanned_at_the_recorded_commit(self):
        c = config(upstream={"git": "https://github.com/o/p.git", "tag_format": None, "submodules": True},
                   source="git", release={"commit": SHA_PIN})
        listed = []

        def list_submodules(url, commit):
            listed.append((url, commit))
            return [("gen", "https://github.com/o/gen.git", SHA_SUB)]
        found = scan.sources("p", c, version="1+git20260101.bbbbbbb", resolve=resolve({}),
                             list_submodules=list_submodules)
        self.assertEqual(listed, [("https://github.com/o/p.git", SHA_PIN)])
        self.assertEqual(found[1], {"name": "p/gen", "url": "https://github.com/o/gen.git", "ref": "",
                                    "commit": SHA_SUB, "cpe_version": None})

    def test_submodules_reads_gitmodules_and_gitlinks_from_a_real_repository(self):
        # A local upstream with one submodule, fetched the way security-scan fetches GitHub
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e",
                       GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@e", GIT_CONFIG_COUNT="1",
                       GIT_CONFIG_KEY_0="protocol.file.allow", GIT_CONFIG_VALUE_0="always")

            def git(*args):
                return subprocess.run(["git", *map(str, args)], check=True, capture_output=True, text=True,
                                      env=env).stdout.strip()
            for repo in ("sub", "up"):
                git("init", "-q", tmp / repo)
                (tmp / repo / "f").write_text(repo)
                git("-C", tmp / repo, "add", "f")
                git("-C", tmp / repo, "commit", "-qm", repo)
            sub_commit = git("-C", tmp / "sub", "rev-parse", "HEAD")
            git("-C", tmp / "up", "submodule", "add", "-q", tmp / "sub", "gen")
            git("-C", tmp / "up", "commit", "-qm", "add gen")
            up_commit = git("-C", tmp / "up", "rev-parse", "HEAD")
            old_env = os.environ.copy()
            os.environ.update(env)
            try:
                self.assertEqual(scan.submodules(str(tmp / "up"), up_commit), [("gen", str(tmp / "sub"), sub_commit)])
            finally:
                os.environ.clear()
                os.environ.update(old_env)


SOURCE = {"name": "p", "url": "https://github.com/o/p.git", "ref": "v1.0", "commit": SHA_TAG, "cpe_version": "1.0"}


class VulnerabilitiesTest(unittest.TestCase):
    def test_osv_is_asked_about_the_commit_and_the_tag_and_fetches_each_advisory_once(self):
        api = FakeApi({
            ("POST", f"{scan.OSV_API}/querybatch"): (200, {"results": [
                {"vulns": [{"id": "GHSA-1"}]}, {"vulns": [{"id": "GHSA-1"}, {"id": "OSV-2"}]}]}),
            ("GET", f"{scan.OSV_API}/vulns/GHSA-1"): (200, {"id": "GHSA-1", "aliases": ["CVE-2026-1"], "summary": "bad",
                                                         "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N"}]}),
            ("GET", f"{scan.OSV_API}/vulns/OSV-2"): (200, {"id": "OSV-2", "details": "worse"}),
        })
        found = scan.osv_vulnerabilities(SOURCE, api)
        self.assertEqual(api.requests[0][2], {"queries": [
            {"commit": SHA_TAG}, {"package": {"ecosystem": "GIT", "name": "https://github.com/o/p"}, "version": "v1.0"}]})
        self.assertEqual([v["id"] for v in found], ["GHSA-1", "OSV-2"])
        self.assertEqual(found[0]["aliases"], ["CVE-2026-1"])
        self.assertEqual(found[0]["severity"], "CVSS:3.1/AV:N")
        self.assertEqual(found[1]["summary"], "worse")

    def test_an_untagged_source_is_asked_about_by_commit_only(self):
        api = FakeApi({("POST", f"{scan.OSV_API}/querybatch"): (200, {"results": [{}]})})
        self.assertEqual(scan.osv_vulnerabilities({**SOURCE, "ref": ""}, api), [])
        self.assertEqual(api.requests[0][2], {"queries": [{"commit": SHA_TAG}]})

    def nvd(self, match):
        return f"{scan.NVD_API}?{urllib.parse.urlencode({'virtualMatchString': match})}"

    @staticmethod
    def cve(cve_id, text, metrics=None):
        return {"cve": {"id": cve_id, "descriptions": [{"lang": "es", "value": "otro"}, {"lang": "en", "value": text}],
                        "metrics": metrics or {}}}

    def test_nvd_is_asked_about_the_cpe_at_the_release_version(self):
        api = FakeApi({
            ("GET", self.nvd("cpe:2.3:a:o:p:1.2.0_rc1")): (200, {"vulnerabilities": [self.cve(
                "CVE-2026-1", "bad", {"cvssMetricV31": [{"cvssData": {"vectorString": "CVSS:3.1/AV:N"}}]})]}),
            ("GET", self.nvd("cpe:2.3:a:o:p")): (200, {"vulnerabilities": []}),
        })
        pauses = []
        found = scan.nvd_vulnerabilities({**SOURCE, "cpe_version": "1.2.0~rc1"}, "cpe:2.3:a:o:p", api,
                                         pause=lambda: pauses.append(1))
        self.assertEqual(found, [{"id": "CVE-2026-1", "aliases": [], "summary": "bad", "severity": "CVSS:3.1/AV:N",
                                  "url": "https://nvd.nist.gov/vuln/detail/CVE-2026-1"}])
        self.assertEqual(len(pauses), 2)

    def test_a_cve_whose_description_names_the_version_counts_despite_its_range(self):
        # As NVD records MOOS-IvP's 2026 CVEs: "through 24.8.1" in words, versionEndExcluding 24.8.1 in the range
        api = FakeApi({
            ("GET", self.nvd("cpe:2.3:a:o:p:24.8.1")): (200, {"vulnerabilities": []}),
            ("GET", self.nvd("cpe:2.3:a:o:p")): (200, {"vulnerabilities": [
                self.cve("CVE-2026-1", "p through 24.8.1 contains an overflow."),
                self.cve("CVE-2026-2", "p before 24.8.10 contains an overflow."),
                self.cve("CVE-2026-3", "p 124.8.1 and 24.8.1.2 are affected."),
            ]}),
        })
        found = scan.nvd_vulnerabilities({**SOURCE, "cpe_version": "24.8.1"}, "cpe:2.3:a:o:p", api)
        self.assertEqual([v["id"] for v in found], ["CVE-2026-1"])

    def test_nvd_is_skipped_without_a_cpe_or_a_version(self):
        api = FakeApi({})
        self.assertEqual(scan.nvd_vulnerabilities(SOURCE, None, api), [])
        self.assertEqual(scan.nvd_vulnerabilities({**SOURCE, "cpe_version": None}, "cpe:2.3:a:o:p", api), [])
        self.assertEqual(api.requests, [])

    def test_an_advisory_osv_and_nvd_both_report_is_listed_once(self):
        osv = [{"id": "GHSA-1", "aliases": ["CVE-2026-1"]}]
        nvd = [{"id": "CVE-2026-1", "aliases": []}, {"id": "CVE-2026-2", "aliases": []}]
        self.assertEqual([v["id"] for v in scan.merge_vulnerabilities(osv, nvd)], ["GHSA-1", "CVE-2026-2"])


class ScanTest(unittest.TestCase):
    def test_each_cpe_is_asked_about_the_release_and_nvd_is_paced_without_a_key(self):
        api = FakeApi({
            ("POST", f"{scan.OSV_API}/querybatch"): (200, {"results": [{}, {}]}),
            ("GET", f"{scan.NVD_API}?virtualMatchString=cpe%3A2.3%3Aa%3Aone%3Ap%3A1.0"): (200, {"vulnerabilities": [
                {"cve": {"id": "CVE-2026-1", "descriptions": []}}]}),
            ("GET", f"{scan.NVD_API}?virtualMatchString=cpe%3A2.3%3Aa%3Atwo%3Ap%3A1.0"): (200, {"vulnerabilities": [
                {"cve": {"id": "CVE-2026-1", "descriptions": []}}, {"cve": {"id": "CVE-2026-2", "descriptions": []}}]}),
            ("GET", f"{scan.NVD_API}?virtualMatchString=cpe%3A2.3%3Aa%3Aone%3Ap"): (200, {"vulnerabilities": []}),
            ("GET", f"{scan.NVD_API}?virtualMatchString=cpe%3A2.3%3Aa%3Atwo%3Ap"): (200, {"vulnerabilities": []}),
            ("GET", scan.SCORECARD_API): (404, None),
        })
        c = config(upstream={"git": "https://github.com/o/p.git"}, source="git",
                   security={"cpes": ["cpe:2.3:a:one:p", "cpe:2.3:a:two:p"]})
        pauses = []
        original = scan.project_config.load
        scan.project_config.load = lambda project: c
        try:
            results = scan.scan(["p"], request=api, pause=pauses.append, version="1.0",
                                resolve=resolve({("https://github.com/o/p.git", "refs/tags/v1.0"): SHA_TAG}))
        finally:
            scan.project_config.load = original
        self.assertEqual([v["id"] for v in results[0]["vulnerabilities"]], ["CVE-2026-1", "CVE-2026-2"])
        self.assertEqual(pauses, [6, 6, 6, 6])


class ScorecardTest(unittest.TestCase):
    RESULT = {"score": 6.5, "date": "2026-11-09", "checks": [{"name": "Maintained", "score": 10},
                                                            {"name": "Fuzzing", "score": 0}]}

    def test_the_weekly_dataset_is_used_when_it_has_the_repository(self):
        api = FakeApi({("GET", f"{scan.SCORECARD_API}/github.com/o/p"): (200, self.RESULT)})
        card = scan.scorecard(SOURCE, "/bin/scorecard", api, run=None)
        self.assertEqual(card, {"score": 6.5, "date": "2026-11-09", "via": "api.securityscorecards.dev",
                                "checks": {"Maintained": 10, "Fuzzing": 0}})

    def test_otherwise_scorecard_runs_on_the_repository(self):
        api = FakeApi({("GET", f"{scan.SCORECARD_API}/github.com/o/p"): (404, None)})
        ran = []

        def run(args, **kwargs):
            ran.append(args)
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(self.RESULT))
        card = scan.scorecard(SOURCE, "/bin/scorecard", api, run)
        self.assertEqual(ran[0][:2], ["/bin/scorecard", "--repo=github.com/o/p"])
        self.assertEqual(card["via"], "scorecard")
        self.assertIsNone(scan.scorecard(SOURCE, None, api, run))

    def test_a_check_that_could_not_run_keeps_the_rest_of_the_result(self):
        api = FakeApi({("GET", f"{scan.SCORECARD_API}/github.com/o/p"): (404, None)})
        partial = {"score": 4.5, "checks": [{"name": "Branch-Protection", "score": -1}, {"name": "SAST", "score": 0}]}

        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, stdout=json.dumps(partial),
                                               stderr="one or more checks failed during execution")
        card = scan.scorecard(SOURCE, "/bin/scorecard", api, run)
        self.assertEqual((card["score"], card["checks"]), (4.5, {"Branch-Protection": -1, "SAST": 0}))

    def test_a_failed_scorecard_run_leaves_the_repository_unscored(self):
        api = FakeApi({("GET", f"{scan.SCORECARD_API}/github.com/o/p"): (404, None)})

        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="rate limited")
        self.assertIsNone(scan.scorecard(SOURCE, "/bin/scorecard", api, run))

    def test_a_repository_outside_github_is_not_scored(self):
        self.assertIsNone(scan.scorecard({**SOURCE, "url": "https://git.example.org/p.git"}, None, FakeApi({})))


RESULTS = [
    {**SOURCE, "project": "p", "scorecard": {"score": 6.5, "date": "", "via": "", "checks": {"Fuzzing": 0, "SAST": 7}},
     "vulnerabilities": [{"id": "GHSA-1", "aliases": ["CVE-2026-1"], "summary": "bad", "severity": "",
                          "url": "https://osv.dev/vulnerability/GHSA-1"}]},
    {"name": "q", "project": "q", "url": "https://github.com/o/q", "ref": "", "commit": SHA_PIN, "cpe_version": None,
     "scorecard": None, "vulnerabilities": []},
]


class ReportTest(unittest.TestCase):
    def test_report_lists_each_source_with_its_score_and_advisories(self):
        text = scan.report(RESULTS)
        self.assertIn("| p | [https://github.com/o/p](https://github.com/o/p) v1.0 (aaaaaaa) | 6.5 / 10 | "
                      "[GHSA-1](https://osv.dev/vulnerability/GHSA-1) |", text)
        self.assertIn("| q | [https://github.com/o/q](https://github.com/o/q) bbbbbbb | not scored | none known |", text)
        self.assertIn("- p: Fuzzing 0", text)
        self.assertNotIn("SAST", text)


class IssuesTest(unittest.TestCase):
    def api(self, issues):
        return FakeApi({
            ("GET", f"{scan.GITHUB_API}/repos/o/build/issues"): (200, issues),
            ("PATCH", f"{scan.GITHUB_API}/repos/o/build/issues/"): (200, {}),
            ("POST", f"{scan.GITHUB_API}/repos/o/build/issues"): (201, {}),
        })

    def test_the_report_issue_is_opened_once_then_updated(self):
        api = self.api([])
        scan.update_issues([], "o/build", "the report", "token", api)
        created = [body for method, _, body in api.requests if method == "POST"]
        self.assertEqual(created[0]["title"], scan.REPORT_TITLE)
        self.assertTrue(created[0]["body"].endswith("the report"))

        api = self.api([{"number": 7, "title": scan.REPORT_TITLE, "state": "open"}])
        scan.update_issues([], "o/build", "newer", "token", api)
        self.assertEqual([(m, u.rsplit("/", 1)[1]) for m, u, _ in api.requests if m != "GET"], [("PATCH", "7")])

    def test_each_advisory_gets_one_issue_even_after_it_is_closed(self):
        api = self.api([{"number": 7, "title": scan.REPORT_TITLE, "state": "open"}])
        opened = scan.update_issues(RESULTS, "o/build", "report", "token", api)
        self.assertEqual(opened, ["Security: p is affected by GHSA-1"])
        body = [b for m, _, b in api.requests if m == "POST"][0]["body"]
        self.assertIn("CVE-2026-1", body)
        self.assertIn(SHA_TAG, body)

        closed = {"number": 8, "title": "Security: p is affected by GHSA-1", "state": "closed"}
        api = self.api([{"number": 7, "title": scan.REPORT_TITLE, "state": "open"}, closed])
        self.assertEqual(scan.update_issues(RESULTS, "o/build", "report", "token", api), [])

    def test_pull_requests_in_the_issue_list_are_ignored(self):
        api = self.api([{"number": 9, "title": scan.REPORT_TITLE, "state": "open", "pull_request": {}}])
        scan.update_issues([], "o/build", "report", "token", api)
        self.assertEqual([body["title"] for m, _, body in api.requests if m == "POST"], [scan.REPORT_TITLE])


class RealProjectsTest(unittest.TestCase):
    def test_every_cpe_names_an_application(self):
        for name in scan.project_config.list_projects():
            for cpe in scan.project_config.load(name)["security"]["cpes"]:
                with self.subTest(project=name, cpe=cpe):
                    self.assertRegex(cpe, r"^cpe:2\.3:a:[^:]+:[^:]+$")

    def test_every_project_has_a_github_upstream_to_scan(self):
        for name in scan.project_config.list_projects():
            with self.subTest(project=name):
                url = scan.project_config.load(name)["upstream"].get("git")
                self.assertTrue(url and scan.github_slug(url), f"{name}: no GitHub upstream.git")


if __name__ == "__main__":
    unittest.main()
