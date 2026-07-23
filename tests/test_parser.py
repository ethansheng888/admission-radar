from __future__ import annotations

import unittest

from admission_radar.fetcher import FetchError, parse_cufe_master


PAGE_URL = "https://gs.cufe.edu.cn/zsgz/sszs_sz_.htm"


class CufeParserTests(unittest.TestCase):
    def test_extracts_full_title_link_and_date(self) -> None:
        html = """
        <html><body>
          <div class="inner_s1">
            <ul>
              <li>
                <a href="../info/1028/7076.htm"
                   title="2026年硕士和博士研究生新生入学须知">
                  <time><span>07-17</span>2026</time>
                  <h3>2026年硕士和博士研究生新生入学须...</h3>
                </a>
              </li>
            </ul>
          </div>
        </body></html>
        """.encode("utf-8")

        notices = parse_cufe_master(html, PAGE_URL)

        self.assertEqual(len(notices), 1)
        self.assertEqual(
            notices[0].title,
            "2026年硕士和博士研究生新生入学须知",
        )
        self.assertEqual(
            notices[0].url,
            "https://gs.cufe.edu.cn/info/1028/7076.htm",
        )
        self.assertEqual(notices[0].published_date, "2026-07-17")

    def test_ignores_navigation_links(self) -> None:
        html = """
        <div class="inner_s1">
          <ul>
            <li><a href="../index.htm" title="首页">首页</a></li>
            <li>
              <a href="../info/1028/1000.htm" title="有效公告">
                <time><span>01-02</span>2026</time>
              </a>
            </li>
          </ul>
        </div>
        """.encode("utf-8")

        notices = parse_cufe_master(html, PAGE_URL)

        self.assertEqual([notice.title for notice in notices], ["有效公告"])

    def test_raises_when_structure_no_longer_matches(self) -> None:
        with self.assertRaises(FetchError):
            parse_cufe_master(b"<html><body>changed</body></html>", PAGE_URL)


if __name__ == "__main__":
    unittest.main()
