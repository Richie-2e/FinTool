"""
extraction/llm/test_candidate_validator.py
Unit tests for candidate_validator.py.
Stage 1: normalization + fragment parsing.
Stage 2: ordered-subsequence fragment search.

Uses stdlib unittest -- no pytest dependency, consistent with this project's
dependency-free validator requirement. Run with:
    python -m unittest extraction.llm.test_candidate_validator -v

Several fixtures below are real evidence strings and real page text captured
from live Ollama output / extraction.parser.parse_pdf() during the Post-L1
benchmark investigation (LICHSGFIN, TataSteel, JioFin), not synthetic
examples, so they double as regression tests against the exact defects that
motivated this module's design. Page text is embedded as static strings
rather than parsed live so the suite stays fast, isolated, and doesn't
require the source PDFs or PyMuPDF to run.
"""

from __future__ import annotations

import unittest

from extraction.metric_extractor import CandidateMetric

from extraction.llm.candidate_validator import (
    EvidenceGroundingResult,
    RejectionRecord,
    ValueGroundingResult,
    _FragmentMatch,
    _OrderedSearchResult,
    _classify_fragment,
    _construct_grounded_region,
    _normalize_for_grounding,
    _numbers_in,
    _ordered_fragment_search,
    _split_evidence_fragments,
    check_evidence_grounding,
    check_value_grounding,
    validate_candidates,
)


class TestNormalizeForGrounding(unittest.TestCase):

    def test_collapses_real_newlines(self):
        self.assertEqual(
            _normalize_for_grounding("Total Equity\n36256.78"),
            "total equity 36256.78",
        )

    def test_collapses_literal_backslash_n_artifact(self):
        # The exact artifact observed in real LICHSGFIN evidence output: a
        # literal two-character "\n" (backslash + n), not a real newline.
        raw = "110.08\\nTotal Equity"
        self.assertEqual(_normalize_for_grounding(raw), "110.08 total equity")

    def test_strips_digit_adjacent_commas(self):
        self.assertEqual(_normalize_for_grounding("2,509.44"), "2509.44")
        self.assertEqual(_normalize_for_grounding("1,32,516.66"), "132516.66")

    def test_preserves_commas_in_prose(self):
        self.assertEqual(
            _normalize_for_grounding("Property, Plant and Equipment"),
            "property, plant and equipment",
        )

    def test_lowercases(self):
        self.assertEqual(_normalize_for_grounding("Total Assets"), "total assets")

    def test_collapses_repeated_whitespace_and_strips(self):
        self.assertEqual(_normalize_for_grounding("  Total   Assets  \n\n "), "total assets")

    def test_real_lichsgfin_fragment_with_mixed_newline_types(self):
        # Exact string captured from a live Ollama response this session.
        raw = (
            "(3) EQUITY\n(a) Equity Share Capital\n110.08\n110.08"
            "\\nTotal Equity\n36256.78\n31394.63"
        )
        normalized = _normalize_for_grounding(raw)
        # Every newline variant collapsed to a single space; no literal
        # backslash/n characters should survive.
        self.assertNotIn("\\n", normalized)
        self.assertNotIn("\n", normalized)
        self.assertIn("total equity", normalized)
        self.assertIn("36256.78", normalized)


class TestSplitEvidenceFragments(unittest.TestCase):

    def test_splits_on_real_newlines(self):
        self.assertEqual(
            _split_evidence_fragments("Total Equity\n36256.78\n31394.63"),
            ["Total Equity", "36256.78", "31394.63"],
        )

    def test_splits_on_literal_backslash_n(self):
        self.assertEqual(
            _split_evidence_fragments("110.08\\nTotal Equity"),
            ["110.08", "Total Equity"],
        )

    def test_drops_empty_fragments(self):
        self.assertEqual(
            _split_evidence_fragments("Total Equity\n\n36256.78\n"),
            ["Total Equity", "36256.78"],
        )

    def test_strips_whitespace_per_fragment(self):
        self.assertEqual(
            _split_evidence_fragments("  Total Equity  \n  36256.78  "),
            ["Total Equity", "36256.78"],
        )

    def test_empty_evidence_returns_empty_list(self):
        self.assertEqual(_split_evidence_fragments(""), [])

    def test_real_lichsgfin_evidence_mixed_newlines(self):
        # Regression fixture: real Ollama output for LICHSGFIN total_liabilities,
        # captured this session, with both real newlines and the literal
        # backslash-n artifact in the same string.
        raw = (
            "(1) Financial Liabilities\nTotal Financial Liabilities\n"
            "277150.05\n259166.89\\n(2) Non-Financial Liabilities\n"
            "Total Non-Financial Liabilities\n519.73\n606.00"
        )
        fragments = _split_evidence_fragments(raw)
        self.assertEqual(
            fragments,
            [
                "(1) Financial Liabilities",
                "Total Financial Liabilities",
                "277150.05",
                "259166.89",
                "(2) Non-Financial Liabilities",
                "Total Non-Financial Liabilities",
                "519.73",
                "606.00",
            ],
        )

    def test_real_tatasteel_gross_profit_evidence(self):
        # Regression fixture: real Ollama output for TataSteel's fabricated
        # gross_profit candidate -- a long, real, multi-line evidence blob.
        raw = (
            "(a)\nCost of materials consumed\n44,088.93\n(b)\n"
            "Purchases of stock-in-trade\n9,825.50"
        )
        fragments = _split_evidence_fragments(raw)
        self.assertEqual(
            fragments,
            [
                "(a)",
                "Cost of materials consumed",
                "44,088.93",
                "(b)",
                "Purchases of stock-in-trade",
                "9,825.50",
            ],
        )


class TestClassifyFragment(unittest.TestCase):

    def test_text_fragment(self):
        self.assertEqual(_classify_fragment("Total Equity"), "text")
        self.assertEqual(_classify_fragment("(2) Non-Financial Assets"), "text")

    def test_numeric_fragment_plain(self):
        self.assertEqual(_classify_fragment("36256.78"), "numeric")

    def test_numeric_fragment_with_comma(self):
        self.assertEqual(_classify_fragment("2,509.44"), "numeric")

    def test_numeric_fragment_negative_in_parens(self):
        self.assertEqual(_classify_fragment("(4,521)"), "numeric")

    def test_numeric_fragment_short_note_reference(self):
        # A bare note-reference number, e.g. the "17" in
        # "Revenue from operations\n17\n68,468" -- must classify as numeric,
        # not text, so it can never anchor the ordered-subsequence search.
        self.assertEqual(_classify_fragment("17"), "numeric")

    def test_junk_fragment_empty_after_normalization(self):
        self.assertEqual(_classify_fragment("---"), "junk")
        self.assertEqual(_classify_fragment("   "), "junk")

    def test_mixed_alphanumeric_note_cross_reference_classifies_as_text(self):
        # "F94" is a real page/note cross-reference observed in TataSteel
        # evidence ("24\nF94\n1,32,516.66"). It contains a letter, so per
        # the frozen classification rule (>=1 alphabetic char => text) it
        # is classified as text, not numeric. Documented here as known,
        # intentional behavior per spec, not an oversight -- a short mixed
        # fragment like this could in principle anchor the ordered search
        # and match spuriously if "f94" recurs elsewhere on a page, but
        # this is the same category of residual risk already accepted for
        # any short text fragment, not something Stage 1 changes.
        self.assertEqual(_classify_fragment("F94"), "text")


# ---------------------------------------------------------------------------
# Stage 2 fixtures: real page text captured via extraction.parser.parse_pdf()
# during the Post-L1 benchmark investigation. Embedded as static strings
# (rather than parsed live in the test) so the suite stays fast, isolated,
# and dependency-free -- it must not require the source PDFs or PyMuPDF to
# be present just to run.
# ---------------------------------------------------------------------------

LICHSGFIN_PAGE_217 = 'STANDALONE BALANCE SHEET\nAS AT MARCH 31, 2025\n(` in crore)\nNote\nAs at \nMarch 31, 2025\nAs at \nMarch 31, 2024\nASSETS\n(1) Financial Assets\n(a) Cash and Cash Equivalents\n5\n1,278.75\n1,437.49\n(b) Bank Balance other than (a) above\n6\n125.77\n135.14\n(c) Loans\n7\n302,845.84\n280,589.79\n(d) Investments\n8\n7,142.14\n6,277.03\n(e) Other Financial Assets\n9\n24.62\n20.85\n\t\nTotal Financial Assets\n311,417.12\n288,460.30\n(2) Non-Financial Assets\n(a) Current Tax Assets (Net)\n10\n586.42\n450.19\n(b) Deferred Tax Assets (Net)\n11\n1,298.85\n1,639.15\n(c) Property, Plant and Equipment\n12.1\n161.79\n171.54\n(d) Capital Work in Progress\n12.2\n0.09\n0.14\n(e) Intangible Assets under Development\n12.3\n51.41\n-\n(f) Right of Use Assets\n12.4\n164.76\n157.47\n(g) Other Intangible Assets\n12.5\n23.96\n31.84\n(h) Other Non-Financial Assets\n13\n222.16\n256.89\n\t\nTotal Non-Financial Assets\n2,509.44\n2,707.22\n\t\nTotal Assets\n313,926.56\n291,167.52\n\t\nLIABILITIES AND EQUITY\n\t\nLIABILITIES\n(1)Financial Liabilities\n(a) Lease Liabilities\n178.02\n164.72\n(b) Payables\n14\n(A)\tTrade Payables\n(i)\n\t\x07Total outstanding dues of micro enterprises and small enterprises\n1.93\n0.02\n(ii) \t\x07Total outstanding dues of creditors other than micro enterprises and \nsmall enterprises\n228.05\n193.43\n(B)\t Other Payables\n(i)\n\t\x07Total outstanding dues of micro enterprises and small enterprises\n-\n-\n(ii) \t\x07Total outstanding dues of creditors other than micro enterprises and \nsmall enterprises\n-\n-\n(c) Debt Securities\n15\n161,631.46\n144,665.32\n(d) Borrowings (Other than Debt Securities)\n16\n98,925.98\n96,136.58\n(e) Deposits\n17\n8,242.92\n9,898.56\n(f) Subordinated Liabilities\n18\n1,796.86\n1,796.33\n(g) Other Financial Liabilities\n19\n6,144.83\n6,311.93\n\t\nTotal Financial Liabilities\n277,150.05\n259,166.89\n(2) Non-Financial Liabilities\n(a) Provisions\n20\n360.43\n334.13\n(b) Other Non-Financial Liabilities\n21\n159.30\n271.87\n\t\nTotal Non-Financial Liabilities\n519.73\n606.00\n(3) EQUITY\n(a) Equity Share Capital\n22\n110.08\n110.08\n(b) Other Equity\n23\n36,146.70\n31,284.55\n\t\nTotal Equity\n36,256.78\n31,394.63\n\t\nTotal Liabilities and Equity\n313,926.56\n291,167.52\nSee accompanying notes forming part of the Standalone Financial Statement 1 - 56\nAs per our report of even date attached\nFor and on behalf of the Board of Directors\nFor SGCO & Co. LLP\nChartered Accountants\nFRN 112081W / W100184\nFor Khandelwal Jain & Co\nChartered Accountants\nFRN 105049W\nSiddhartha Mohanty\nChairman\nDIN : 08058830\nKashi Prasad Khandelwal\nDirector\nDIN : 00748523\nT. Adhikari\nManaging Director & \nChief Executive Officer\nDIN : 10229197\nSuresh Murarka\nPartner\nM. No. 044739\nShailesh Shah\nPartner\nM.No. 033632\nVarsha Hardasani\nCompany Secretary\nACS No.: 50448\nLokesh Mundhra\nCFO\nH. J. Panchariya\nGeneral Manager\n(Accounts)\nPlace: Mumbai\nDate : May 15, 2025\nLIC Housing Finance Limited  |  Annual Report 2024-25\n214\nStandalone Financial Statements\n'

TATASTEEL_PAGE_283 = 'STATEMENT OF PROFIT AND LOSS\nfor the year ended March 31, 2025\n(I crore)\nNote\nPage\nYear ended \nMarch 31, 2025\nYear ended \nMarch 31, 2024 \n(Restated)\nI\nRevenue from operations\n24\nF94\n1,32,516.66\n1,40,932.65\nII\nOther income\n25\nF95\n2,246.90\n3,113.49\nIII\nTotal income\n1,34,763.56\n1,44,046.14\nIV\nExpenses:\n(a)\nCost of materials consumed\n44,088.93\n48,516.26\n(b)\nPurchases of stock-in-trade\n9,825.50\n9,699.77\n(c)\nChanges in inventories of finished and semi-finished goods, stock-in-trade and  \nwork-in-progress\n26\nF95\n330.66\n379.91\n(d)\nEmployee benefits expense\n27\nF96\n8,010.08\n7,472.52\n(e)\nFinance costs\n28\nF96\n4,238.35\n4,100.52\n(f)\nDepreciation and amortisation expense\n29\nF96\n6,253.16\n6,008.95\n(g)\nOther expenses\n30\nF97\n43,170.46\n45,863.02\n1,15,917.14\n1,22,040.95\n(h)\nLess: Expenditure (other than finance cost) transferred to capital account\n774.46\n987.54\nTotal expenses\n1,15,142.68\n1,21,053.41\nV\nProfit before exceptional items and tax (III-IV)\n19,620.88\n22,992.73\nVI\nExceptional items:\n31\nF98\n(a)\nProvision for impairment of investments/doubtful loans and advances/other financial assets\n(74.91)\n(2,823.70)\n(b)\nProvision for impairment of non-current assets\n-\n(178.91)\n(c)\nEmployee separation compensation (net)\n(670.78)\n(98.83)\n(d)\nRestructuring and other provisions\n-\n(404.67)\n(e)\nGain/(loss) on non-current investments classified as fair value through profit and loss (net)\n16.76\n18.09\n(f)\nContribution to electoral trusts\n(173.11)\n-\nTotal exceptional items\n(902.04)\n(3,488.02)\nVII\nProfit before tax (V+VI)\n18,718.84\n19,504.71\nVIII Tax expense:\n9\nF68\n(a)\nCurrent tax\n3,765.51\n4,383.47\n(b)\nDeferred tax\n983.63\n(540.61)\nTotal tax expense\n4,749.14\n3,842.86\nIX\nProfit for the year (VII-VIII)\n13,969.70\n15,661.85\nX\nOther comprehensive income\nA.\n(i)\nItems that will not be reclassified to profit and loss\n(a)\t Remeasurement gain/(loss) on post-employment defined benefit plans\n(168.92)\n(213.39)\n(b)\t Fair value changes of investments in equity shares\n(23,729.01)\n(8,711.87)\n(ii) Income tax on items that will not be reclassified to profit and loss\n18.78\n(59.42)\nB.\n(i)\nItems that will be reclassified to profit and loss\n(a)\t Fair value changes of cash flow hedges\n(125.62)\n(58.83)\n(ii) Income tax on items that will be reclassified to profit and loss\n31.61\n15.14\nTotal other comprehensive income for the year\n(23,973.16)\n(9,028.37)\nXI\nTotal comprehensive income for the year (IX+X)\n(10,003.46)\n6,633.48\nXII\nEarnings per share\n32\nF99\nBasic (I)\n11.19\n12.55\nDiluted (I)\n11.19\n12.54\nNotes forming part of the standalone financial statements\n1-51\nIn terms of our report attached\nFor and on behalf of the Board of Directors\nFor Price Waterhouse & Co\nsd/-\nsd/-\nsd/-\nsd/- \nsd/- \nsd/-\nChartered Accountants LLP\nN. Chandrasekaran\nNoel Naval Tata\nDeepak Kapoor\nV. K. Sharma\nBharti Gupta Ramola\nShekhar C. Mande\nFirm Registration Number: \nChairman\nVice-Chairman\nIndependent Director\nIndependent Director \nDIN: 02449088\nIndependent Director \nDIN: 00356188\nIndependent Director \nDIN: 10083454\n304026E/E-300009\nDIN: 00121863\nDIN: 00024713\nDIN: 00162957\nsd/-\nsd/-\nsd/-\nsd/-\nsd/-\nsd/-\nSubramanian Vivek\nPramod Agrawal\nSaurabh Agrawal\nT. V. Narendran\nKoushik Chatterjee\nParvatheesam Kanchinadham\nPartner \nMembership Number 100332\nIndependent \nDirector \nDIN: 00279727\nNon-Executive \nDirector \nDIN: 02144558\nChief Executive Officer \n& Managing Director \nDIN: 03083605\nExecutive Director \n& Chief Financial Officer \nDIN: 00004989\nCompany Secretary and  \nChief Legal Officer  \nACS: 15921\nMumbai, May 12, 2025\nFINANCIAL STATEMENTS\n118th Year  |  Integrated Report & Annual Accounts 2024-25  |  F27\n'

JIOFIN_PAGE_83 = '\t\n\t\n\t\n\t\n\t\n\t\n\t\n₹ in crore\nParticulars\nAs at \n31st March, 2025\nAs at \n31st March, 2024\nASSETS\nFinancial Assets\n   Cash and Cash equivalents\n1\n 8.63 \n 4.95 \n   Bank Balances other than Cash and Cash equivalents\n2\n 549.52 \n 4,585.25 \n   Loans\n3\n 1,747.98 \n 91.63 \n   Investments\n4\n22,706.17 \n 19,719.97 \n   Other financial assets\n5\n 20.14 \n 6.25 \nTotal Financial Assets\n25,032.44 \n 24,408.05 \nNon-financial assets\n   Current tax assets (Net)\n6\n 25.29 \n 24.30 \n   Property, Plant and Equipment\n7.A\n 24.67 \n 29.95 \n   Other Intangible assets\n7.B\n 3.29 \n 6.58 \n   Other non-financial assets\n8\n 9.84 \n 4.95 \nTotal Non-financial assets\n 63.09 \n 65.78 \nTotal Assets\n25,095.53 \n 24,473.83 \nLIABILITIES AND EQUITY\nLiabilities\nFinancial Liabilities\nPayables\n9\n  Trade Payables\n    Total outstanding dues of micro enterprises and small enterprises \n 1.20 \n 1.01 \n    Total outstanding dues of creditors other than micro enterprises \n 5.11 \n 1.38 \n    and small enterprises\nOther financial liabilities\n10\n 2.40 \n 1.18 \nTotal Financial Liabilities\n 8.71 \n 3.57 \nNon-Financial Liabilities\n   Provisions\n11\n 14.81 \n 11.76 \n   Deferred tax liabilities (Net)\n12\n 76.78 \n 17.18 \n   Other non-financial liabilities\n13\n 9.77 \n 4.51 \nTotal Non-Financial Liabilities\n 101.36 \n 33.45 \nTotal Liabilities\n 110.07 \n 37.02 \nEquity\nEquity Share capital\n14\n 6,353.14 \n 6,353.28 \nOther Equity\n15\n 18,632.32 \n 18,083.53 \nTotal Equity\n 24,985.46 \n 24,436.81 \nTotal Liabilities and Equity\n25,095.53 \n 24,473.83 \nMaterial accounting policies\nA-E\nAccompanying Notes to Standalone Financial Statements\n1 to 42\nStandalone Balance sheet\nAs at 31st March, 2025\nAs per our Report of Even Date\t\n\t\nFor and on Behalf of the Board\nFor LODHA & CO LLP\t\n\t\nK. V. Kamath\t\nIsha M. Ambani\nChartered Accountants\t\n\t\nNon-Executive Chairman\t\nNon-Executive Director\nFRN: 301051E/E300284\t\n\t\nDIN: 00043501\t\nDIN: 06984175\nR. P. Singh\t\nAbhishek Haridas Pathak\t\nHitesh Kumar Sethia\t\nRajiv Mehrishi\nPartner\t\nGroup Chief Financial Officer\t\nManaging Director & \t\nNon-Executive Director\nM. No.: 052438\t\n\t\nChief Executive Officer\t\n\t\n\t\nDIN: 09250710\t\nDIN: 00208189\nFor Deloitte Haskins & Sells\t\nMohana V\t\nSunil Mehta\t\nBimal Manu Tanna\nChartered Accountants\t\nGroup Company Secretary\t\nNon-Executive Director\t\nNon-Executive Director\nFRN: 117365W\t\n\t\nDIN: 07430460\t\nDIN: 06767157\nVishal L. Parekh\t\n\t\nRama Vedashree\t\nAnshuman Thakur\nPartner\t\n\t\nNon-Executive Director\t\nNon-Executive Director\nM. No.: 113918\t\n\t\nDIN: 10412547\t\nDIN: 03279460\nDate: April 17, 2025\nFINANCIAL STATEMENTS\nSTATUTORY REPORTS\nCORPORATE OVERVIEW\n122\n123\nJIO FINANCIAL SERVICES LIMITED l ANNUAL REPORT F Y 2024-25\nJIO FINANCIAL SERVICES LIMITED l ANNUAL REPORT F Y 2024-25\n'


class TestOrderedFragmentSearch(unittest.TestCase):

    def test_lichsgfin_non_contiguous_current_assets_case(self):
        # The exact case that motivated dropping a hard locality constraint
        # from the frozen design: real evidence for LICHSGFIN current_assets
        # was "(2) Non-Financial Assets\nTotal Non-Financial Assets\n2509.44\n2707.22"
        # -- but the real page has 8 intervening line items (410 chars) between
        # the section header and the total. Both fragments are genuinely real
        # and in order; this MUST succeed under the frozen no-locality-bound design.
        text_fragments = ["(2) Non-Financial Assets", "Total Non-Financial Assets"]
        normalized_page = _normalize_for_grounding(LICHSGFIN_PAGE_217)
        result = _ordered_fragment_search(text_fragments, normalized_page)
        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.matches), 2)
        gap = result.matches[1].start - result.matches[0].end
        self.assertGreater(gap, 300, "expected a large non-contiguous gap, confirming this isn't a trivial adjacent match")
        # Order/monotonicity invariant: each match starts no earlier than
        # the previous one ended.
        self.assertLessEqual(result.matches[0].end, result.matches[1].start)

    def test_lichsgfin_total_liabilities_fabricated_value_but_real_text_fragments(self):
        # Real evidence for LICHSGFIN's fabricated total_liabilities candidate
        # quoted real label text ("Total Financial Liabilities", "Total
        # Non-Financial Liabilities") even though the VALUE it declared
        # (291166.89) was fabricated -- actually the prior-year total_assets
        # figure from a completely different row. The text fragments
        # themselves are genuinely grounded, so this search must SUCCEED --
        # confirming L4 (fragment grounding) and L5 (value grounding) catch
        # different things, exactly as the frozen design intends. The
        # fabrication is L5's job, not this function's.
        # Note: the source PDF text is inconsistent between sections --
        # "(1) Financial Assets" (with a space) but "(1)Financial
        # Liabilities" (no space) -- so the fragment below intentionally
        # matches the liabilities section's exact real formatting.
        text_fragments = [
            "(1)Financial Liabilities",
            "Total Financial Liabilities",
            "(2) Non-Financial Liabilities",
            "Total Non-Financial Liabilities",
        ]
        normalized_page = _normalize_for_grounding(LICHSGFIN_PAGE_217)
        result = _ordered_fragment_search(text_fragments, normalized_page)
        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.matches), 4)

    def test_tatasteel_gross_profit_fabricated_value_but_real_text_fragments(self):
        # Real evidence for TataSteel's fabricated gross_profit candidate
        # (8706.94, not traceable to any real number on the page) quoted six
        # genuinely real, correctly-ordered expense line items. Same point
        # as above: the text-fragment search succeeds; only value grounding
        # (a later stage) should reject this candidate.
        text_fragments = [
            "Cost of materials consumed",
            "Purchases of stock-in-trade",
            "Employee benefits expense",
            "Finance costs",
            "Depreciation and amortisation expense",
            "Other expenses",
        ]
        normalized_page = _normalize_for_grounding(TATASTEEL_PAGE_283)
        result = _ordered_fragment_search(text_fragments, normalized_page)
        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.matches), 6)

    def test_fails_when_fragment_does_not_exist_on_page(self):
        # "Gross profit/(loss)" is the exact label the model echoed as a
        # raw_label for TataSteel's fabricated gross_profit candidate -- but
        # TataSteel's income statement has no such line at all (it goes
        # straight from Total Income to Profit before tax). Must fail.
        text_fragments = ["Gross profit/(loss)"]
        normalized_page = _normalize_for_grounding(TATASTEEL_PAGE_283)
        result = _ordered_fragment_search(text_fragments, normalized_page)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.failed_fragment, "Gross profit/(loss)")
        self.assertEqual(result.matches, [])

    def test_fails_on_out_of_order_fragments(self):
        # Same two real fragments as the non-contiguous test above, but
        # deliberately reversed relative to their true page order. The
        # ordered search must reject this -- this is the exact mechanism
        # that would catch a model quoting fragments out of reading order.
        text_fragments = ["Total Non-Financial Assets", "(2) Non-Financial Assets"]
        normalized_page = _normalize_for_grounding(LICHSGFIN_PAGE_217)
        result = _ordered_fragment_search(text_fragments, normalized_page)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.failed_fragment, "(2) Non-Financial Assets")
        # The first fragment ("Total Non-Financial Assets") still matches
        # before the failure -- confirms partial progress is preserved for
        # diagnostics.
        self.assertEqual(len(result.matches), 1)

    def test_duplicate_fragment_matches_distinct_occurrences(self):
        # "As at" appears twice in LICHSGFIN's real column header ("As at
        # March 31, 2025 / As at March 31, 2024"). Searching for it twice in
        # a row must advance the cursor and match two DISTINCT occurrences,
        # not the same span twice.
        text_fragments = ["As at", "As at"]
        normalized_page = _normalize_for_grounding(LICHSGFIN_PAGE_217)
        result = _ordered_fragment_search(text_fragments, normalized_page)
        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.matches), 2)
        self.assertNotEqual(result.matches[0].start, result.matches[1].start)
        self.assertLessEqual(result.matches[0].end, result.matches[1].start)

    def test_simple_contiguous_case_still_works(self):
        # Regression sanity check: a straightforward single-fragment case
        # that strict substring matching already handled correctly before
        # this redesign must still pass.
        text_fragments = ["Total Assets"]
        normalized_page = _normalize_for_grounding(JIOFIN_PAGE_83)
        result = _ordered_fragment_search(text_fragments, normalized_page)
        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.matches), 1)

    def test_fragment_is_normalized_before_matching(self):
        # Individual fragments passed in are raw/unnormalized (extra
        # whitespace, mixed case) -- the function must normalize each one
        # internally (reusing Stage 1's _normalize_for_grounding) before
        # searching, even though normalized_text_block is pre-normalized by
        # the caller.
        text_fragments = ["  TOTAL non-financial ASSETS  "]
        normalized_page = _normalize_for_grounding(LICHSGFIN_PAGE_217)
        result = _ordered_fragment_search(text_fragments, normalized_page)
        self.assertTrue(result.succeeded)

    def test_empty_fragment_list_fails_with_no_failed_fragment(self):
        # Distinct from "a fragment failed to match": there was never an
        # anchor to search for in the first place.
        result = _ordered_fragment_search([], _normalize_for_grounding(JIOFIN_PAGE_83))
        self.assertFalse(result.succeeded)
        self.assertIsNone(result.failed_fragment)
        self.assertEqual(result.matches, [])


class TestConstructGroundedRegion(unittest.TestCase):

    def test_returns_none_when_search_failed(self):
        failed_result = _OrderedSearchResult(succeeded=False, matches=[], failed_fragment="X")
        self.assertIsNone(_construct_grounded_region(failed_result, "irrelevant text block"))

    def test_reuses_positions_without_recomputing(self):
        # Deliberately construct a normalized_text_block where the fragment
        # strings recorded in the matches do NOT actually occur at the given
        # start/end positions (they occur elsewhere, or not at all). If
        # _construct_grounded_region re-searched instead of reusing the
        # given positions, this test would behave differently than a pure
        # slice -- it must not: it slices exactly [start:end] regardless of
        # what the fragment text says.
        text_block = "aaaa bbbb cccc dddd eeee"
        fake_result = _OrderedSearchResult(
            succeeded=True,
            matches=[
                _FragmentMatch(fragment="this text does not appear in text_block", start=5, end=9),
                _FragmentMatch(fragment="neither does this", start=15, end=19),
            ],
            failed_fragment=None,
        )
        region = _construct_grounded_region(fake_result, text_block)
        self.assertEqual(region, text_block[5:19])
        self.assertEqual(region, "bbbb cccc dddd")

    def test_lichsgfin_non_contiguous_region_spans_the_real_gap(self):
        text_fragments = ["(2) Non-Financial Assets", "Total Non-Financial Assets"]
        normalized_page = _normalize_for_grounding(LICHSGFIN_PAGE_217)
        search_result = _ordered_fragment_search(text_fragments, normalized_page)
        self.assertTrue(search_result.succeeded)

        region = _construct_grounded_region(search_result, normalized_page)
        self.assertEqual(
            region,
            normalized_page[search_result.matches[0].start : search_result.matches[-1].end],
        )
        # The region must contain the real intervening content (the 8 skipped
        # line items), not just the two matched fragments concatenated.
        self.assertGreater(len(region), 300)
        self.assertIn("current tax assets", region)  # one of the skipped intervening lines

    def test_region_boundary_does_not_extend_past_last_text_fragment(self):
        # Documented, currently-correct-per-spec characteristic, found while
        # implementing this stage: since the region ends at the last TEXT
        # fragment's end (not the last fragment of any kind), a trailing
        # number that immediately follows the last label -- the normal
        # pattern in these documents -- falls just OUTSIDE the region. This
        # is flagged for Stage 4 (value grounding) to account for; not
        # changed here, since it matches the frozen spec exactly as written.
        text_fragments = ["(2) Non-Financial Assets", "Total Non-Financial Assets"]
        normalized_page = _normalize_for_grounding(LICHSGFIN_PAGE_217)
        search_result = _ordered_fragment_search(text_fragments, normalized_page)
        region = _construct_grounded_region(search_result, normalized_page)
        self.assertTrue(region.endswith("total non-financial assets"))
        self.assertNotIn("2509.44", region)  # the value itself -- just past the boundary


class TestCheckEvidenceGrounding(unittest.TestCase):

    def test_lichsgfin_current_assets_non_contiguous_case_passes(self):
        # Exact real evidence captured for LICHSGFIN's current_assets
        # candidate this session.
        evidence = "(2) Non-Financial Assets\nTotal Non-Financial Assets\n2509.44\n2707.22"
        result = check_evidence_grounding(evidence, LICHSGFIN_PAGE_217)
        self.assertIsInstance(result, EvidenceGroundingResult)
        self.assertTrue(result.passed)
        self.assertEqual(result.reason, "grounded")
        self.assertEqual(result.matched_fragments, ["(2) Non-Financial Assets", "Total Non-Financial Assets"])
        # Per test_region_boundary_does_not_extend_past_last_text_fragment
        # above: the region is grounded and spans the real non-contiguous
        # gap, but does NOT include the trailing value numbers -- that is
        # Stage 4's concern, not asserted here.
        self.assertTrue(result.grounded_region.endswith("total non-financial assets"))

    def test_tatasteel_gross_profit_fabricated_value_passes_l4_anyway(self):
        # Exact real evidence captured for TataSteel's fabricated gross_profit
        # candidate. All six quoted expense line items are genuinely real and
        # in order, so L4 passes -- the fabrication (declared value 8706.94
        # does not correspond to any of these numbers) is L5's job, a later
        # stage. This confirms L4 and L5 are independent checks, as intended.
        evidence = (
            "(a)\nCost of materials consumed\n44,088.93\n(b)\n"
            "Purchases of stock-in-trade\n9,825.50\n(c)\n"
            "Changes in inventories of finished and semi-finished goods, "
            "stock-in-trade and work-in-progress\n26\nF95\n330.66\n(d)\n"
            "Employee benefits expense\n27\nF96\n8,010.08\n(e)\n"
            "Finance costs\n28\nF96\n4,238.35\n(f)\n"
            "Depreciation and amortisation expense\n29\nF96\n6,253.16\n(g)\n"
            "Other expenses\n30\nF97\n43,170.46"
        )
        result = check_evidence_grounding(evidence, TATASTEEL_PAGE_283)
        self.assertTrue(result.passed)
        self.assertEqual(result.reason, "grounded")
        # Not asserting an exact fragment count: short reference-code
        # fragments like "(a)", "(b)", "F95", "F96" also contain a letter
        # and so classify as "text" per _classify_fragment's rule, in
        # addition to the 7 real descriptive labels -- all of them are
        # genuinely real and in order on this page, so all correctly match.
        # Asserting the real labels are present is the meaningful check.
        real_labels = [
            "Cost of materials consumed",
            "Purchases of stock-in-trade",
            "Employee benefits expense",
            "Finance costs",
            "Depreciation and amortisation expense",
            "Other expenses",
        ]
        for label in real_labels:
            self.assertIn(label, result.matched_fragments)

    def test_empty_evidence_fails(self):
        result = check_evidence_grounding("", LICHSGFIN_PAGE_217)
        self.assertFalse(result.passed)
        self.assertIsNone(result.grounded_region)
        self.assertEqual(result.reason, "no_text_anchor_in_evidence")

    def test_bare_numeric_evidence_fails_no_text_anchor(self):
        # After classification, a purely numeric evidence string has zero
        # text fragments to anchor the search -- must fail the same way as
        # empty evidence, not attempt to ground on numbers alone.
        result = check_evidence_grounding("36256.78", LICHSGFIN_PAGE_217)
        self.assertFalse(result.passed)
        self.assertIsNone(result.grounded_region)
        self.assertEqual(result.reason, "no_text_anchor_in_evidence")

    def test_fabricated_nonexistent_label_fails(self):
        # "Gross profit/(loss)" does not appear anywhere on TataSteel's
        # income statement page (it has no such line at all).
        result = check_evidence_grounding("Gross profit/(loss)", TATASTEEL_PAGE_283)
        self.assertFalse(result.passed)
        self.assertIsNone(result.grounded_region)
        self.assertEqual(result.failed_fragment, "Gross profit/(loss)")
        self.assertIn("Gross profit/(loss)", result.reason)

    def test_jiofin_simple_real_case_passes(self):
        evidence = "Total Assets\n25,095.53 \n24,473.83"
        result = check_evidence_grounding(evidence, JIOFIN_PAGE_83)
        self.assertTrue(result.passed)
        self.assertEqual(result.matched_fragments, ["Total Assets"])
        # Single text fragment -> region is just that label (see
        # test_region_boundary_does_not_extend_past_last_text_fragment);
        # the value itself is outside the region, by the same boundary
        # characteristic, flagged for Stage 4.
        self.assertEqual(result.grounded_region, "total assets")


class TestNumbersIn(unittest.TestCase):

    def test_extracts_and_parses_multiple_numbers(self):
        self.assertEqual(_numbers_in("Total Equity 36256.78 31394.63"), [36256.78, 31394.63])

    def test_reuses_parse_value_for_negative_parens(self):
        # Confirms reuse of metric_extractor.parse_value(), not a
        # reimplementation: parentheses mean negative.
        self.assertEqual(_numbers_in("(4521)"), [-4521.0])

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(_numbers_in("total equity"), [])


class TestCheckValueGrounding(unittest.TestCase):

    def test_lichsgfin_current_assets_boundary_case_now_passes(self):
        # The exact case from Stage 3 that exposed the boundary issue:
        # grounded_region ends at "...total non-financial assets", value
        # 2509.44 sits 1 char past that boundary. Must now pass via the
        # minimal trailing extension, without L4 itself changing.
        evidence = "(2) Non-Financial Assets\nTotal Non-Financial Assets\n2509.44\n2707.22"
        gr = check_evidence_grounding(evidence, LICHSGFIN_PAGE_217)
        self.assertTrue(gr.passed)  # L4 unaffected
        result = check_value_grounding(2509.44, gr.grounded_region, LICHSGFIN_PAGE_217)
        self.assertTrue(result.passed)
        self.assertFalse(result.degraded)
        self.assertEqual(result.matched_number, 2509.44)
        self.assertEqual(result.reason, "value_grounded")

    def test_jiofin_total_assets_boundary_case_now_passes(self):
        evidence = "Total Assets\n25,095.53 \n24,473.83"
        gr = check_evidence_grounding(evidence, JIOFIN_PAGE_83)
        self.assertTrue(gr.passed)
        result = check_value_grounding(25095.53, gr.grounded_region, JIOFIN_PAGE_83)
        self.assertTrue(result.passed)
        self.assertEqual(result.matched_number, 25095.53)

    def test_tatasteel_revenue_note_reference_gap_passes(self):
        # Real 8-character gap case (note-reference number + page
        # cross-reference between label and value) -- within the 40-char
        # extension.
        evidence = "I\nRevenue from operations\n24\nF94\n1,32,516.66"
        gr = check_evidence_grounding(evidence, TATASTEEL_PAGE_283)
        self.assertTrue(gr.passed)
        result = check_value_grounding(132516.66, gr.grounded_region, TATASTEEL_PAGE_283)
        self.assertTrue(result.passed)
        self.assertEqual(result.matched_number, 132516.66)

    def test_tatasteel_gross_profit_fabrication_correctly_rejected(self):
        # Real fabricated candidate: L4 passes (all quoted expense line
        # items are genuinely real and in order) but the declared value
        # (8706.94) does not correspond to any of them or to anything
        # within the trailing extension. This is the central case
        # demonstrating L4 and L5 catch different things.
        evidence = (
            "(a)\nCost of materials consumed\n44,088.93\n(b)\n"
            "Purchases of stock-in-trade\n9,825.50\n(c)\n"
            "Changes in inventories of finished and semi-finished goods, "
            "stock-in-trade and work-in-progress\n26\nF95\n330.66\n(d)\n"
            "Employee benefits expense\n27\nF96\n8,010.08\n(e)\n"
            "Finance costs\n28\nF96\n4,238.35\n(f)\n"
            "Depreciation and amortisation expense\n29\nF96\n6,253.16\n(g)\n"
            "Other expenses\n30\nF97\n43,170.46"
        )
        gr = check_evidence_grounding(evidence, TATASTEEL_PAGE_283)
        self.assertTrue(gr.passed)
        result = check_value_grounding(8706.94, gr.grounded_region, TATASTEEL_PAGE_283)
        self.assertFalse(result.passed)
        self.assertFalse(result.degraded)
        self.assertIsNone(result.matched_number)
        self.assertEqual(result.reason, "value_not_grounded")

    def test_lichsgfin_total_liabilities_cross_row_fabrication_correctly_rejected(self):
        # Real fabricated candidate: L4 passes (real liability-section
        # fragments, in order) but the declared value (291166.89) is
        # actually the PRIOR-YEAR TOTAL ASSETS figure from a completely
        # different row, far away on the page -- not within the region or
        # its minimal trailing extension.
        evidence = (
            "(1)Financial Liabilities\nTotal Financial Liabilities\n"
            "277150.05\n259166.89\n(2) Non-Financial Liabilities\n"
            "Total Non-Financial Liabilities\n519.73\n606.00"
        )
        gr = check_evidence_grounding(evidence, LICHSGFIN_PAGE_217)
        self.assertTrue(gr.passed)
        result = check_value_grounding(291166.89, gr.grounded_region, LICHSGFIN_PAGE_217)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "value_not_grounded")

    def test_extension_does_not_reach_a_distant_unrelated_number(self):
        # Confirms the 40-char extension is bounded, not unbounded. Note:
        # "Total Assets" (313926.56) turned out NOT to be a good fixture for
        # this -- it is structurally the very next line after "Total
        # Non-Financial Assets" (Total Assets = Total Financial Assets +
        # Total Non-Financial Assets), so it sits well within 40 chars. That
        # is itself a real, worth-noting residual risk of a fixed trailing
        # extension: tightly-packed adjacent subtotal lines can fall inside
        # the window too. Using total_equity's value instead here, which is
        # measured at 1112 chars past this region's end -- genuinely distant.
        evidence = "(2) Non-Financial Assets\nTotal Non-Financial Assets"
        gr = check_evidence_grounding(evidence, LICHSGFIN_PAGE_217)
        self.assertTrue(gr.passed)
        result = check_value_grounding(36256.78, gr.grounded_region, LICHSGFIN_PAGE_217)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "value_not_grounded")

    def test_degraded_mode_when_l4_failed(self):
        # grounded_region=None simulates an L4 failure. L5 still runs, in
        # degraded mode, against the full text_block -- for diagnostics
        # only. The caller (a later orchestration stage), not this
        # function, is responsible for not treating a degraded pass as
        # acceptance.
        result = check_value_grounding(25095.53, None, JIOFIN_PAGE_83)
        self.assertTrue(result.passed)
        self.assertTrue(result.degraded)

    def test_degraded_mode_value_not_on_page_fails(self):
        result = check_value_grounding(999999.99, None, JIOFIN_PAGE_83)
        self.assertFalse(result.passed)
        self.assertTrue(result.degraded)

    def test_sign_mismatch_detected(self):
        evidence = "Total Assets\n25,095.53 \n24,473.83"
        gr = check_evidence_grounding(evidence, JIOFIN_PAGE_83)
        # Real value on the page is +25095.53; declaring it negative should
        # be flagged as a sign mismatch, not a generic "not grounded".
        result = check_value_grounding(-25095.53, gr.grounded_region, JIOFIN_PAGE_83)
        self.assertFalse(result.passed)
        self.assertTrue(result.sign_mismatch)
        self.assertEqual(result.matched_number, 25095.53)
        self.assertEqual(result.reason, "sign_mismatch")

    def test_value_within_tolerance_passes(self):
        evidence = "Total Assets\n25,095.53 \n24,473.83"
        gr = check_evidence_grounding(evidence, JIOFIN_PAGE_83)
        # 0.01% off -- well within the 2% relative tolerance.
        result = check_value_grounding(25095.53 * 1.0001, gr.grounded_region, JIOFIN_PAGE_83)
        self.assertTrue(result.passed)

    def test_grounded_region_not_found_in_text_block_falls_back_defensively(self):
        # Defensive path: grounded_region is provided but doesn't actually
        # occur in text_block (should not happen in practice, since L4
        # derives it from the same text_block via slicing). Must not crash;
        # falls back to searching the region alone.
        result = check_value_grounding(123.0, "this string is not in the page", JIOFIN_PAGE_83)
        self.assertFalse(result.passed)
        self.assertFalse(result.degraded)


def _make_candidate(
    metric_name, raw_label, value, evidence,
    statement_type="income_statement", doc_id="d1", year=2025, page_no=283,
):
    return CandidateMetric(
        doc_id=doc_id, metric_name=metric_name, raw_label=raw_label, value=value,
        unit="INR crore", year=year, page_no=page_no, statement_type=statement_type,
        section_type=statement_type, confidence="high", source="llm_extraction",
        evidence=evidence,
    )


class TestValidateCandidates(unittest.TestCase):

    def test_fully_passing_candidate_is_accepted(self):
        # Real JioFin total_assets: correct label, correct evidence, correct value.
        candidate = _make_candidate(
            "total_assets", "Total Assets", 25095.53,
            "Total Assets\n25,095.53 \n24,473.83", statement_type="balance_sheet",
        )
        accepted, diagnostics = validate_candidates([candidate], JIOFIN_PAGE_83)
        self.assertEqual(accepted, [candidate])
        self.assertEqual(len(diagnostics), 1)
        d = diagnostics[0]
        self.assertTrue(d.l2_passed)
        self.assertTrue(d.l4_passed)
        self.assertTrue(d.l5_passed)
        self.assertFalse(d.l5_degraded)
        self.assertEqual(d.rejection_reasons, [])

    def test_l2_only_failure_real_hint_echo_case(self):
        # Real TataSteel net_profit regression case from the Post-L1
        # benchmark: raw_label is hint-echoed text ("Profit for the year,
        # profit for the period, profit after tax") and fails L2, but the
        # real evidence ("IX\nProfit for the year (VII-VIII)\n13,969.70")
        # and value (13969.7, genuinely correct per the earlier benchmark
        # investigation) pass L4 and L5 cleanly. Confirms L2 is independent
        # of L4/L5, exactly as the frozen design intends -- a candidate can
        # be numerically and evidentially correct while still failing on
        # label grounding alone.
        candidate = _make_candidate(
            "net_profit",
            "Profit for the year, profit for the period, profit after tax",
            13969.7,
            "IX\nProfit for the year (VII-VIII)\n13,969.70",
        )
        accepted, diagnostics = validate_candidates([candidate], TATASTEEL_PAGE_283)
        self.assertEqual(accepted, [])
        d = diagnostics[0]
        self.assertFalse(d.l2_passed)
        self.assertTrue(d.l4_passed)
        self.assertTrue(d.l5_passed)
        self.assertFalse(d.l5_degraded)
        self.assertEqual(d.rejection_reasons, ["raw_label_mismatch"])

    def test_l4_failure_cascades_to_degraded_l5(self):
        # "Gross profit/(loss)" passes L2 (after the regex fix) but does not
        # appear anywhere on TataSteel's income statement page at all --
        # L4 fails, and L5 runs in degraded (diagnostic-only) mode as a
        # consequence, never counting toward acceptance.
        candidate = _make_candidate("gross_profit", "Gross profit/(loss)", 8706.94, "Gross profit/(loss)")
        accepted, diagnostics = validate_candidates([candidate], TATASTEEL_PAGE_283)
        self.assertEqual(accepted, [])
        d = diagnostics[0]
        self.assertTrue(d.l2_passed)
        self.assertFalse(d.l4_passed)
        self.assertFalse(d.l5_passed)
        self.assertTrue(d.l5_degraded)
        self.assertEqual(len(d.rejection_reasons), 1)
        self.assertTrue(d.rejection_reasons[0].startswith("evidence_not_grounded:"))
        # No redundant value-grounding reason when L4 already failed.
        self.assertFalse(any(r.startswith("value_not_grounded") for r in d.rejection_reasons))

    def test_l5_only_failure_real_fabrication_case(self):
        # Real TataSteel gross_profit fabrication: L2 passes (post regex
        # fix), L4 passes (all six quoted expense line items are genuinely
        # real and in order), but the declared value does not correspond to
        # any of them -- L5 correctly rejects it. This is the case that
        # motivated keeping L2/L4/L5 as independent, all-run checks.
        candidate = _make_candidate(
            "gross_profit", "Gross profit/(loss)", 8706.94,
            "(a)\nCost of materials consumed\n44,088.93\n(b)\n"
            "Purchases of stock-in-trade\n9,825.50\n(c)\n"
            "Changes in inventories of finished and semi-finished goods, "
            "stock-in-trade and work-in-progress\n26\nF95\n330.66\n(d)\n"
            "Employee benefits expense\n27\nF96\n8,010.08\n(e)\n"
            "Finance costs\n28\nF96\n4,238.35\n(f)\n"
            "Depreciation and amortisation expense\n29\nF96\n6,253.16\n(g)\n"
            "Other expenses\n30\nF97\n43,170.46",
        )
        accepted, diagnostics = validate_candidates([candidate], TATASTEEL_PAGE_283)
        self.assertEqual(accepted, [])
        d = diagnostics[0]
        self.assertTrue(d.l2_passed)
        self.assertTrue(d.l4_passed)
        self.assertFalse(d.l5_passed)
        self.assertFalse(d.l5_degraded)
        self.assertEqual(d.rejection_reasons, ["value_not_grounded:value_not_grounded"])

    def test_mixed_batch_returns_only_fully_passing_candidates_in_order(self):
        # A realistic mixed batch from a single statement-type call: one
        # clean pass, one L2-only failure, one L5-only failure, in that
        # order. Confirms accepted preserves relative order and contains
        # exactly the passing candidate; diagnostics has one entry per
        # input candidate regardless of outcome.
        c_pass = _make_candidate("revenue", "Revenue from operations", 132516.66, "I\nRevenue from operations\n24\nF94\n1,32,516.66")
        c_l2fail = _make_candidate(
            "net_profit", "Profit for the year, profit for the period, profit after tax",
            13969.7, "IX\nProfit for the year (VII-VIII)\n13,969.70",
        )
        c_l5fail = _make_candidate(
            "gross_profit", "Gross profit/(loss)", 8706.94,
            "(a)\nCost of materials consumed\n44,088.93\n(b)\n"
            "Purchases of stock-in-trade\n9,825.50\n(c)\n"
            "Changes in inventories of finished and semi-finished goods, "
            "stock-in-trade and work-in-progress\n26\nF95\n330.66\n(d)\n"
            "Employee benefits expense\n27\nF96\n8,010.08\n(e)\n"
            "Finance costs\n28\nF96\n4,238.35\n(f)\n"
            "Depreciation and amortisation expense\n29\nF96\n6,253.16\n(g)\n"
            "Other expenses\n30\nF97\n43,170.46",
        )
        batch = [c_pass, c_l2fail, c_l5fail]
        accepted, diagnostics = validate_candidates(batch, TATASTEEL_PAGE_283)

        self.assertEqual(accepted, [c_pass])
        self.assertEqual(len(diagnostics), 3)
        self.assertEqual([d.metric_name for d in diagnostics], ["revenue", "net_profit", "gross_profit"])
        self.assertTrue(diagnostics[0].l2_passed and diagnostics[0].l4_passed and diagnostics[0].l5_passed)
        self.assertFalse(diagnostics[1].l2_passed)
        self.assertFalse(diagnostics[2].l5_passed)

    def test_empty_batch_returns_empty_results(self):
        accepted, diagnostics = validate_candidates([], TATASTEEL_PAGE_283)
        self.assertEqual(accepted, [])
        self.assertEqual(diagnostics, [])

    def test_diagnostic_record_preserves_candidate_metadata(self):
        candidate = _make_candidate(
            "revenue", "Revenue from operations", 132516.66,
            "I\nRevenue from operations\n24\nF94\n1,32,516.66",
            doc_id="e36efb6efe4f", year=2025,
        )
        _, diagnostics = validate_candidates([candidate], TATASTEEL_PAGE_283)
        d = diagnostics[0]
        self.assertEqual(d.doc_id, "e36efb6efe4f")
        self.assertEqual(d.statement_type, "income_statement")
        self.assertEqual(d.metric_name, "revenue")
        self.assertEqual(d.raw_label, "Revenue from operations")
        self.assertEqual(d.value, 132516.66)
        self.assertEqual(d.year, 2025)


if __name__ == "__main__":
    unittest.main()
