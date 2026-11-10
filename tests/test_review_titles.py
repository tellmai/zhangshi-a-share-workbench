import copy
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from qsa.review import ReviewStore, build_review, render_cover
from qsa.review_titles import title_plan
from tests.test_review import fixture


class ReviewTitleTests(unittest.TestCase):
    def test_big_index_move_and_actual_sector_replace_generic_title(self):
        sector=dict(name='信息安全',avg_pct=6.89,breadth=100,available_count=11,member_count=11)
        plan=title_plan({'创业板指':{'pct':4.16}},[sector],{})
        self.assertEqual(plan['selected_title'],'创业板涨4.16%，信息安全能延续吗？')
        self.assertEqual(len(plan['options']),3)
        self.assertTrue(all('4.16%' in v['title'] and '信息安全' in v['title'] for v in plan['options']))
        self.assertIn('样本',plan['scope'])
        self.assertEqual(plan['evidence'][1]['value'],6.89)

    def test_real_index_divergence_requires_both_directions(self):
        plan=title_plan({'创业板指':{'pct':2.3},'上证指数':{'pct':-.81}},[],{})
        self.assertEqual(plan['angle'],'指数分化')
        self.assertIn('创业板涨2.30%',plan['selected_title'])
        self.assertIn('沪指跌0.81%',plan['selected_title'])
        together=title_plan({'创业板指':{'pct':2.3},'上证指数':{'pct':.81}},[],{})
        self.assertNotEqual(together['angle'],'指数分化')

    def test_dedup_never_removes_current_market_facts(self):
        indices={'上证指数':{'pct':1.27}}
        history=[]
        for _ in range(8):
            plan=title_plan(indices,[],{},history)
            self.assertIn('1.27%',plan['selected_title'])
            self.assertNotIn('盘后线索梳理',plan['selected_title'])
            self.assertNotIn(plan['selected_title'],[r['title'] for r in history[:5]])
            history.insert(0,{'title':plan['selected_title']})

    def test_sector_only_headline_keeps_sample_scope_and_stats_have_no_fake_comparison(self):
        sector=dict(name='测试题材',avg_pct=3.21,breadth=90,available_count=9,member_count=9)
        plan=title_plan({},[sector],{})
        self.assertIn('样本涨3.21%',plan['selected_title'])
        self.assertIn('非全市场',plan['scope'])
        facts={'turnover_yuan':dict(value=1.95e12,scope='沪深成交额',source_ids=['a','b'])}
        plan=title_plan({},[],facts)
        self.assertIn('1.95万亿',plan['selected_title'])
        self.assertFalse(any(word in plan['selected_title'] for word in ('放量','缩量','暴增','逆转')))

    def test_unverified_index_does_not_leak_into_title(self):
        data,ctx=fixture()
        ctx['indices']['创业板指']={'pct':99,'close':1234,'published_at':ctx['trade_date']+'T15:01:00+08:00','status':'单源'}
        report=build_review(data,ctx)
        self.assertEqual(report['status'],'draft')
        self.assertNotIn('99.00%',str(report['title_design']))
        self.assertIn('1.00%',report['title'])

    def test_cover_typography_preserves_both_lines_and_escapes_text(self):
        plan=title_plan({'创业板指':{'pct':4.16}},[],{})
        plan['support']='测试<依据>'
        svg=render_cover(plan['selected_title'],'2026-10-23',design=plan)
        root=ElementTree.fromstring(svg)
        self.assertEqual((root.attrib['width'],root.attrib['height']),('1920','1080'))
        texts=root.findall('.//{http://www.w3.org/2000/svg}text')
        headlines=[t for t in texts if t.attrib.get('font-weight')=='800']
        self.assertEqual([t.text for t in headlines],plan['selected_lines'])
        self.assertTrue(all(int(t.attrib['font-size'])<=112 for t in headlines))
        self.assertIn('&lt;依据&gt;',svg)

    def test_selecting_alternative_preserves_original_evidence_and_exports_consistently(self):
        data,ctx=fixture()
        report=build_review(data,ctx)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            store=ReviewStore(temp)
            old=store.save(report)
            choice=copy.deepcopy(old['title_design']['options'][1])
            revised=store.choose_title(old['id'],1)
            self.assertNotEqual(revised['id'],old['id'])
            self.assertEqual(store.read(old['id'])['title'],old['title'])
            for field in ('body','sources','cutoff_at','snapshot'):self.assertEqual(revised[field],old[field])
            self.assertEqual(revised['title'],choice['title'])
            folder=store.root/revised['id']
            self.assertTrue((folder/'douyin.txt').read_text(encoding='utf-8').startswith(choice['title']))
            svg=(folder/'cover.svg').read_text(encoding='utf-8')
            self.assertTrue(all(line in svg for line in choice['lines']))
            self.assertEqual(store.choose_title(revised['id'],1)['id'],revised['id'])
            for bad in (-1,9,True,'1'):
                with self.assertRaises(ValueError):store.choose_title(old['id'],bad)


if __name__=='__main__':unittest.main()
