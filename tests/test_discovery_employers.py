"""Public portal boundaries that can hide jobs or misstate eligibility."""
import json
from urllib.parse import parse_qs, urlsplit

from kitty.config import EligibilityRules
from kitty.core.discovery.eligibility import evaluate
from kitty.core.discovery.employers import fetch_board, parse_jibe, parse_oracle, parse_siemens
from kitty.core.discovery.http import HttpError, RateLimited
from kitty.core.discovery.public_pages import PublicPage


def page(url, value):
    return PublicPage(url, json.dumps(value), "2026-10-04T00:00:00Z")


def test_employer_pagination_retains_later_intern_and_reports_failed_query():
    board = {"company": "Chip Maker", "kind": "jibe", "url": "https://chips.example/jobs",
             "api_url": "https://chips.example"}

    class Pages:
        def fetch(self, url):
            params = parse_qs(urlsplit(url).query)
            if params["keywords"] == ["student"]:
                raise HttpError("unavailable")
            rows = [{"data": {"req_id": str(i), "title": "Senior Engineer"}} for i in range(20)]
            if params["page"] == ["2"]:
                rows = [{"data": {"req_id": "intern-21", "title": "RTL Design Intern",
                                  "city": "Austin", "state": "Texas", "country": "United States",
                                  "description": "Design RTL with the processor team."}}]
            return page(url, {"jobs": rows, "totalCount": 21})

    jobs, problems = fetch_board(Pages(), board, ["intern", "student"], 4, 50)
    assert [job.title for job in jobs] == ["RTL Design Intern"]
    assert jobs[0].location == "Austin, Texas, United States"
    assert problems == ["student: unavailable"]


def test_eightfold_stops_on_rate_limit_without_discarding_earlier_detail():
    board = {"company": "Aero", "kind": "eightfold", "url": "https://aero.example/careers",
             "api_url": "https://aero.example", "domain": "aero.example"}

    class Pages:
        limited = False

        def fetch(self, url):
            assert not self.limited, "a 429 must stop this board's remaining queries"
            rows = [{"id": i, "name": "Firmware Intern", "standardizedLocations": ["Austin, TX, US"],
                     "positionUrl": f"/careers/job/{i}"} for i in (1, 2)]
            if "position_details" in url:
                if parse_qs(urlsplit(url).query)["position_id"] == ["1"]:
                    return page(url, {"data": rows[0] | {"jobDescription": "Develop firmware."}})
                self.limited = True
                raise RateLimited("429")
            return page(url, {"data": {"positions": rows, "count": 10}})

    # A 429 must not erase the completed first posting or trigger more requests.
    jobs, problems = fetch_board(Pages(), board, ["intern", "student"], 4, 50)
    assert [job.url for job in jobs] == ["https://aero.example/careers/job/1"]
    assert problems == ["rate limited: 429"]


def test_portal_qualifications_reach_eligibility_even_when_separate_from_description():
    for kind, parser, row in [
        ("jibe", parse_jibe, {"req_id": "1", "title": "GPU Intern", "description": "Build a compiler.",
                               "qualifications": "<p>Requires a master's degree.</p>"}),
        ("oracle", parse_oracle, {"Id": "1", "Title": "GPU Intern", "ExternalDescriptionStr": "Build a compiler.",
                                   "ExternalQualificationsStr": "<p>Requires a master's degree.</p>"}),
    ]:
        board = {"company": "Chips", "kind": kind, "url": "https://chips.example/jobs"}
        job = parser(board, row, page(board["url"], {}), "intern")
        verdict = evaluate(title=job.title, description=job.description, location=None, country=None,
                           degrees=None, rules=EligibilityRules(drop_if_requires=["master's degree"]))
        assert verdict.eligibility == "ineligible"


def test_siemens_related_jobs_cannot_pollute_description_or_location():
    text = '''<meta property="og:title" content="Software Engineering Intern">
    <article class="article article--details"><div class="article__content__view__field">
    <div class="article__content__view__field__label">Job ID</div>
    <div class="article__content__view__field__value">42</div></div>
    <div class="article__content__view__field tf_locations">
    <div class="article__content__view__field__label"></div>
    <div class="article__content__view__field__value"><div>Austin, Texas, United States</div></div></div>
    </article><article class="article article--details"><p>Open to undergraduates.</p></article>
    <article id="widgetRelatedJobs"><p>Related job: master's degree required in London</p></article>'''
    board = {"company": "Siemens", "kind": "siemens", "url": "https://jobs.example/externaljobs"}
    job = parse_siemens(board, PublicPage(board["url"] + "/JobDetail/42", text, "2026-10-04T00:00:00Z"), "intern")
    assert job.location == "Austin, Texas, United States"
    verdict = evaluate(title=job.title, description=job.description, location=job.location, country=None,
                       degrees=None, rules=EligibilityRules(drop_if_requires=["master's degree"]))
    assert verdict.eligibility == "eligible"
    assert "Related job" not in job.description


def test_overlapping_query_prefix_does_not_hide_later_unique_postings():
    board = {"company": "Chips", "kind": "jibe", "url": "https://chips.example/jobs",
             "api_url": "https://chips.example"}

    class Pages:
        def fetch(self, url):
            params = parse_qs(urlsplit(url).query)
            rows = [{"data": {"req_id": str(i), "title": "Senior Engineer"}} for i in range(20)]
            total = 20 if params["keywords"] == ["intern"] else 21
            if params["page"] == ["2"]:
                rows = [{"data": {"req_id": "new", "title": "GPU Student Intern", "country_code": "US"}}]
            return page(url, {"jobs": rows, "totalCount": total})

    jobs, problems = fetch_board(Pages(), board, ["intern", "student"], 3, 50)
    assert [job.title for job in jobs] == ["GPU Student Intern"]
    assert problems == []


def test_local_jibe_keeps_later_city_jobs_and_pay_but_excludes_full_time_and_outside_area():
    board = {"company": "Grocer", "kind": "jibe", "url": "https://grocer.example/jobs",
             "api_url": "https://grocer.example", "location": "Waco, TX", "radius": 35,
             "allow_cities": ["Hewitt", "Woodway"]}

    class Pages:
        def fetch(self, url):
            params = parse_qs(urlsplit(url).query)
            assert params["location"] == ["Waco, TX"] and params["radius"] == ["35"]
            rows = [{"data": {"req_id": str(i), "title": "Cashier", "city": "Houston",
                               "state": "Texas"}} for i in range(20)]
            if params["page"] == ["2"]:
                local = {"req_id": "local", "title": "Cashier", "city": "Hewitt",
                         "state": "Texas", "employment_type": "PART_TIME",
                         "tags": ["USD $11.75/Hr."], "description": "Serve customers."}
                rows = [{"data": local}, {"data": local | {"language": "es-mx"}},
                        {"data": local | {"req_id": "full", "employment_type": "FULL_TIME"}}]
            return page(url, {"jobs": rows, "totalCount": 23})

    jobs, problems = fetch_board(Pages(), board, [""], 3, 50)
    assert [job.source_job_id for job in jobs] == ["grocer.example:local"]
    verdict = evaluate(title=jobs[0].title, description=jobs[0].description, location=jobs[0].location,
                       country=None, degrees=None, rules=EligibilityRules(min_hourly_pay=12))
    assert verdict.eligibility == "ineligible"
    assert problems == []


def test_local_hourly_feed_keeps_guaranteed_pay_and_explicit_part_time_only():
    board = {"company": "Pet Shop", "kind": "jibe", "url": "https://pets.example/jobs",
             "allow_cities": ["Waco"], "part_time_only": True}
    hourly = {"req_id": "hourly", "title": "Retail Sales Associate Part Time",
              "city": "Waco", "state": "Texas", "country_code": "US",
              "salary_frequency": "HOURLY", "salary_min_value": 13,
              "salary_max_value": 19, "description": "Assist customers with pet supplies."}

    class Pages:
        def fetch(self, url):
            return page(url, {"jobs": [{"data": hourly}, {"data": hourly | {
                "req_id": "unknown", "title": "Pet Trainer",
            }}], "totalCount": 2})

    jobs, problems = fetch_board(Pages(), board, [""], 2, 10)
    assert [job.title for job in jobs] == ["Retail Sales Associate Part Time"]
    assert "$13 - $19 per hour" in jobs[0].description
    verdict = evaluate(title=jobs[0].title, description=jobs[0].description,
                       location=jobs[0].location, country="US", degrees=None,
                       rules=EligibilityRules(min_hourly_pay=12))
    assert verdict.eligibility == "eligible"
    assert not problems


def test_local_oracle_preserves_uncertain_hours_and_continuous_acceptance():
    board = {"company": "Shop", "kind": "oracle", "url": "https://shop.example/en/sites/CX",
             "site": "CX", "allow_cities": ["Lancaster", "Hewitt"],
             "search_location": {"latitude": 31.5493, "longitude": -97.1467, "radius": 35, "radiusUnit": "MI"}}
    records = {
        "pt": {"Id": "pt", "Title": "SALES ASSOCIATE-PT", "PrimaryLocation": "Lancaster, TX, United States"},
        "unknown": {"Id": "unknown", "Title": "SALES ASSOCIATE", "PrimaryLocation": "Lancaster, TX, United States"},
        "full": {"Id": "full", "Title": "SALES ASSOCIATE-FT", "PrimaryLocation": "Lancaster, TX, United States"},
        "other-state": {"Id": "other-state", "Title": "Cashier", "PrimaryLocation": "Lancaster, PA, United States"},
        "continuous": {"Id": "continuous", "Title": "Grocery Clerk", "JobSchedule": "Part time",
                       "PrimaryLocation": "Hewitt, TX, United States",
                       "ExternalQualificationsStr": "We continuously accept applications for roles like this."},
    }

    class Pages:
        def fetch(self, url):
            finder = parse_qs(urlsplit(url).query)["finder"][0]
            if "recruitingCEJobRequisitionDetails" in url:
                ident = finder.split("Id=")[1].split(",")[0]
                assert ident not in {"full", "other-state"}, "excluded hits must not waste a detail request"
                return page(url, {"items": [records[ident]]})
            assert "latitude=31.5493,longitude=-97.1467,radius=35,radiusUnit=MI" in finder
            return page(url, {"items": [{"requisitionList": list(records.values()), "TotalJobsCount": 5}]})

    jobs, problems = fetch_board(Pages(), board, [""], 3, 50)
    by_id = {job.source_job_id.split(":")[-1]: job for job in jobs}
    assert set(by_id) == {"pt", "unknown", "continuous"}
    assert "eligibility_note" not in by_id["pt"].raw
    assert "confirm hours" in by_id["unknown"].raw["eligibility_note"]
    assert "immediate vacancy is not confirmed" in by_id["continuous"].raw["eligibility_note"]
    assert problems == []


def test_local_search_cap_reports_partial_instead_of_successful_empty():
    board = {"company": "Grocer", "kind": "jibe", "url": "https://grocer.example/jobs",
             "allow_cities": ["Hewitt"]}

    class Pages:
        def fetch(self, url):
            return page(url, {"jobs": [{"data": {"req_id": "outside", "title": "Cashier",
                                                "city": "Houston", "state": "Texas"}}],
                              "totalCount": 25})

    jobs, problems = fetch_board(Pages(), board, [""], 1, 50)
    assert jobs == []
    assert problems == [": page cap 1 reached"]


def test_store_addresses_prevent_same_city_openings_from_merging(workspace):
    from kitty.core.discovery.pipeline import CompanyResolver, SourceTally, store_posting

    track = next(t for t in workspace.config.tracks.tracks if t.location_rules.allow_cities)
    city = track.location_rules.allow_cities[0]
    board = {"company": "Grocer", "kind": "oracle", "url": "https://grocer.example/en/sites/CX",
             "site": "CX", "allow_cities": [city]}
    records = {
        str(i): {"Id": str(i), "Title": "Cashier", "JobSchedule": "Part time",
                 "PrimaryLocation": f"{city}, TX, United States",
                 "workLocation": [{"AddressLine1": address, "TownOrCity": city,
                                   "Region2": "TX", "Country": "US"}]}
        for i, address in enumerate(["100 Market St", "200 Main St"])
    }

    class Pages:
        def fetch(self, url):
            if "recruitingCEJobRequisitionDetails" in url:
                finder = parse_qs(urlsplit(url).query)["finder"][0]
                ident = finder.split("Id=")[1].split(",")[0]
                return page(url, {"items": [records[ident]]})
            return page(url, {"items": [{"requisitionList": list(records.values()), "TotalJobsCount": 2}]})

    jobs, problems = fetch_board(Pages(), board, [""], 3, 50)
    tally = SourceTally()
    for job in jobs:
        job.source = "local_employers"
        store_posting(workspace.conn, job, tracks=workspace.config.tracks.tracks,
                      resolver=CompanyResolver(workspace.config.watchlist),
                      selective_companies=set(), tally=tally)
    assert tally.new == 2 and tally.merged == 0
    assert {job.location for job in jobs} == {f"{address}, {city}, TX, US"
                                             for address in ("100 Market St", "200 Main St")}
    assert problems == []
