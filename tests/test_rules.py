"""Run: python3 -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notify  # noqa: E402
import rules  # noqa: E402
import sources  # noqa: E402


class StudentKind(unittest.TestCase):
    def test_kinds(self):
        for title, kind in [
            ("Praktikum im Bereich Werkstoffe (m/w/d)", "Praktikum"),
            ("Pflichtpraktikum Materialentwicklung", "Praktikum"),
            ("Internship in Metallography", "Praktikum"),
            ("Werkstudent Werkstoffprüfung (w/m/div.)", "Werkstudent"),
            ("Working Student Materials Lab", "Werkstudent"),
            ("Studentische Hilfskraft (HiWi) Polymerchemie", "HiWi"),
            ("Student Assistant Corrosion", "HiWi"),
            ("Studentenjob im Labor", "Studentenjob"),
            ("Senior Materials Engineer", ""),
            ("Materialwissenschaftler (m/w/d)", ""),
        ]:
            self.assertEqual(rules.student_kind(title), kind, title)


class TitleDrops(unittest.TestCase):
    def test_kept(self):
        for t in ["Praktikum Werkstoffprüfung", "Werkstudent Metallografie", "Praktikant / Abschlussarbeit Keramik",
                  "HiWi Polymerforschung"]:
            self.assertIsNone(rules.title_drop(t), t)

    def test_dropped(self):
        for t in ["Ausbildung zum Werkstoffprüfer", "Duales Studium Materialwissenschaft", "Trainee Werkstofftechnik",
                  "Masterarbeit Korrosion", "Werkstoffingenieur (m/w/d)", "PhD position corrosion, student"]:
            self.assertIsNotNone(rules.title_drop(t), t)

    def test_other_field(self):
        self.assertTrue(rules.other_field("Pflichtpraktikum im strategischen Einkauf"))
        self.assertTrue(rules.other_field("Praktikum KI & Surrogate Modeling"))
        self.assertTrue(rules.other_field("Praktikum Qualitätsmanagement Einkauf – Zukaufteile"))
        self.assertFalse(rules.other_field("Praktikum Qualitätsmanagement Werkstoffprüfung"))
        self.assertFalse(rules.other_field("Werkstudent Metallografie"))


class MaterialTerms(unittest.TestCase):
    def test_title_words(self):
        self.assertIn("werkstoff", rules.material_terms("Praktikum in der Werkstoffentwicklung"))
        self.assertIn("metallurgie", rules.material_terms("Internship in Metallography"))
        self.assertIn("polymer", rules.material_terms("Praktikum Kunststoffverfahrenstechnik"))
        self.assertIn("keramik", rules.material_terms("Praktikum - Chemie-/Keramiklabor"))
        self.assertIn("beschichtung", rules.material_terms("Oberflächenbeschichtung"))

    def test_logistics_material_is_not_materials_science(self):
        self.assertEqual(rules.material_terms("Praktikum Materialwirtschaft und Materialplanung"), [])
        self.assertEqual(rules.material_terms("Werkstudent Logistik und Warenfluss"), [])

    def test_distinct_terms(self):
        self.assertEqual(rules.material_terms("Korrosion Korrosion corrosion"), ["korrosion"])


class Names(unittest.TestCase):
    def test_title_key_ignores_gender_marks(self):
        a = rules.title_key("Werkstudent Werkstoffprüfung (w/m/div.)")
        b = rules.title_key("Werkstudent Werkstoffprüfung (m/w/d)")
        self.assertEqual(a, b)

    def test_norm_company(self):
        self.assertEqual(rules.norm_company("Robert Bosch GmbH"), "robertbosch")


class Blacklist(unittest.TestCase):
    def test_app_key_matches_the_apps_normCompany(self):
        # same results as JOB_APP's normCompany(): lower-case, & -> and, only a-z0-9, stop words removed
        self.assertEqual(rules.app_company_key("Robert Bosch"), "robertbosch")
        self.assertEqual(rules.app_company_key("BMW Group"), "bmw")
        self.assertEqual(rules.app_company_key("GMH Gruppe"), "gmhgruppe")
        self.assertEqual(rules.app_company_key("B. Braun"), "bbraun")

    def test_only_german_entries_count(self):
        state = {"blacklist": {"amazon": {"on": True}, "de:robertbosch": {"on": True}, "de:airbus": {"on": False}}}
        self.assertEqual(notify.app_blacklist(state), {"robertbosch"})


class Parsers(unittest.TestCase):
    def test_rss(self):
        xml = ("<rss><channel><item><title>Werkstudent Materialforschung (m/f/d)</title>"
               "<link>https://www.mpg.de/1/x?123</link><description>&lt;p&gt;Alloys&lt;/p&gt;</description>"
               "<pubDate>Fri, 13 Nov 2026 14:49:00 +0100</pubDate></item>"
               "<item><title>PhD positions</title><link>https://www.mpg.de/2/y</link></item></channel></rss>")
        jobs = sources.parse_rss(xml, "Max Planck institutes")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["key"], "rss:https://www.mpg.de/1/x")
        self.assertEqual(jobs[0]["posted"], "2026-11-13T13:49:00Z")
        self.assertEqual(jobs[0]["desc"], "Alloys")

    def test_softgarden(self):
        feed = {"dataFeedElement": [
            {"item": {"title": "Praktikum Polymerchemie", "url": "https://x.softgarden.de/jobs/1/Praktikum?a=b",
                      "description": "<b>Polymer</b>", "jobLocation": {"address": {"addressLocality": "Jülich"}}}},
            {"item": {"title": "Chemielaborant", "url": "https://x.softgarden.de/jobs/2/y"}}]}
        jobs = sources.parse_softgarden(feed, "Forschungszentrum Jülich")
        self.assertEqual([j["title"] for j in jobs], ["Praktikum Polymerchemie"])
        self.assertEqual(jobs[0]["locations"], ["Jülich"])

    def test_source_ids(self):
        self.assertEqual(sources.source_id({"type": "smartrecruiters", "company": "BoschGroup"}), "smartrecruiters:BoschGroup")
        self.assertEqual(sources.source_id({"type": "workday", "tenant": "ag"}), "workday:ag")


class Push(unittest.TestCase):
    def test_only_the_german_list_is_used(self):
        state = {"subs": {"phone": {"on": True, "sub": {"endpoint": "US"}}},
                 "subs_de": {"friend": {"on": True, "sub": {"endpoint": "DE"}},
                             "off": {"on": False, "sub": {"endpoint": "OFF"}}}}
        self.assertEqual(notify.subscriptions(state), [{"endpoint": "DE"}])
        self.assertEqual(notify.subscriptions({"subs": {"a": {"on": True, "sub": {"endpoint": "US"}}}}), [])

    def test_messages(self):
        alerts = [{"hid": str(i), "company": "Bosch", "rank": 4, "autostar": True, "title": "Praktikum Werkstoffe",
                   "role": "Praktikum", "location": "Stuttgart"} for i in range(10)]
        msgs = notify.push_messages(alerts)
        self.assertEqual(len(msgs), notify.PUSH_PER_JOB_MAX + 1)       # 8 single + 1 summary
        self.assertTrue(msgs[0]["top"])
        self.assertIn("Praktikum", msgs[0]["title"])


if __name__ == "__main__":
    unittest.main()
