import unittest
from unittest.mock import patch

import domestic_search


class DomesticSearchTest(unittest.TestCase):
    def test_wechat_public_results_are_parsed_and_deduplicated(self):
        sogou = '''
        <h3><a href="/link?url=abc"><em>AI</em> Agent Skill 工程实践</a></h3>
        <p class="txt-info">包含架构、工具调用和技能复盘。</p>
        '''
        ddg = '''
        <a class="result__a" href="https://mp.weixin.qq.com/s/xyz">大模型前沿研究总结</a>
        <a class="result__snippet">介绍最新模型技术与评测结果。</a>
        '''
        with patch.object(domestic_search, "_get_text", side_effect=[sogou, ddg]):
            rows = domestic_search.search_platform("weixin", "AI Agent", 5)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source_label"], "微信公众号")
        self.assertEqual(rows[0]["access"], "public-no-login")
        self.assertIn("AI Agent Skill", rows[0]["title"])

    def test_non_article_urls_are_rejected(self):
        self.assertFalse(domestic_search._article_url("toutiao", "https://www.toutiao.com/search/?keyword=AI"))
        self.assertTrue(domestic_search._article_url("toutiao", "https://www.toutiao.com/article/123"))
        self.assertTrue(domestic_search._article_url("juejin", "https://juejin.cn/post/123"))

    def test_bing_fallback_keeps_only_platform_articles(self):
        page = '''
        <li class="b_algo"><h2><a href="https://blog.csdn.net/dev/article/details/123"><strong>AI</strong> Agent 工程实践</a></h2><div><p>介绍架构与评测方法。</p></div></li>
        <li class="b_algo"><h2><a href="https://spam.example/post/1">广告</a></h2><p>忽略</p></li>
        '''
        with patch.object(domestic_search, "_get_text", return_value=page):
            rows = domestic_search._search_bing("csdn", "AI Agent", 5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "AI Agent 工程实践")
        self.assertIn("架构与评测", rows[0]["summary"])

    def test_publication_date_parses_relative_and_absolute_chinese_dates(self):
        from datetime import date

        today = date(2026, 8, 5)
        self.assertEqual(domestic_search._publication_date("6小时前 · 新发布", today), today)
        self.assertEqual(domestic_search._publication_date("6 天之前 · 内容", today), date(2026, 7, 30))
        self.assertEqual(domestic_search._publication_date("2026年7月18日 发布", today), date(2026, 7, 18))


if __name__ == "__main__":
    unittest.main()
