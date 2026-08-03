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

import re
import unittest
from typing import Optional

from extraction.metric_extractor import CandidateMetric

from extraction.llm.candidate_validator import (
    EvidenceGroundingResult,
    RejectionRecord,
    ValueGroundingResult,
    _FragmentMatch,
    _OrderedSearchResult,
    _classify_fragment,
    _construct_grounded_region,
    _evidence_self_confirms_value,
    _normalize_evidence_layout,
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

# Captured live via extraction.parser.parse_pdf() + extraction.llm.page_selector
# (select_pages("balance_sheet") -> get_page_block()) during the FV2-1
# pre-implementation verification, 2026-08-02. This is the exact text_block
# candidate_validator.py sees in production for these two documents' balance
# sheets -- not a synthetic example. Confirms the real substring collision
# RC-1 names: "Non-current assets"/"Non-current liabilities" (OFSS) and
# "Non-Current Assets"/"Non-Current Liabilities" (Reliance) each contain
# "current assets"/"current liabilities" as a literal substring, immediately
# preceded by a hyphen, ahead of the real "Current assets"/"Current
# liabilities" heading further down the same page.
OFSS_PAGE_61 = 'Consolidated balance sheet \nas at March 31, 2025\n(Amounts in ₹ million)\nNotes\nMarch 31, 2025\nMarch 31, 2024\nASSETS\nNon-current assets\n\t\nProperty, plant and equipment\n3\n 1,319 \n 1,484 \n\t\nCapital work-in-progress\n3\n 91 \n 27 \n\t\nRight-of-use assets\n4\n 451 \n 378 \n\t\nInvestment property\n5\n 102 \n 102 \n\t\nGoodwill\n6\n 6,087 \n 6,087 \n\t\nFinancial assets\n7\n 65 \n 639 \n\t\nDeferred tax assets (net)\n16\n 1,827 \n 1,863 \n\t\nIncome tax assets (net)\n 10,537 \n 10,905 \n\t\nOther non-current assets\n10\n 1,413 \n 1,358 \n 21,892 \n 22,843 \nCurrent assets\n\t\nFinancial assets\n\t\n\t\nTrade receivables\n8\n 11,837 \n 13,193 \n\t\n\t\nCash and cash equivalents\n9 (a)\n 12,142 \n 34,833 \n\t\n\t\nOther bank balances\n9 (b)\n 47,372 \n 20,549 \n\t\n\t\nOther financial assets\n7\n 3,599 \n 4,323 \n\t\nIncome tax assets (net)\n 619 \n 280 \n\t\nOther current assets\n10\n 3,889 \n 3,336 \n 79,458 \n 76,514 \nTOTAL\n 101,350 \n 99,357 \nEQUITY AND LIABILITIES\nEquity\n\t\nEquity Share capital\n11\n 434 \n 433 \n\t\nOther Equity\n12\n 83,190 \n 78,155 \nTotal equity\n 83,624 \n 78,588 \nNon-current liabilities\n\t\nFinancial liabilities\n\t\n\t\nLease liabilities\n27\n 275 \n 185 \n\t\n\t\nOther financial liabilities\n13\n 22 \n 21 \n\t\nOther non-current liabilities\n14\n 10 \n 189 \n\t\nEmployee benefit obligations\n15\n 2,210 \n 1,853 \n\t\nDeferred tax liability (net)\n16\n 677 \n 2,385 \n\t\nIncome tax liabilities (net)\n 3,023 \n 3,338 \n 6,217 \n 7,971 \nCurrent liabilities\n\t\nFinancial liabilities\n\t\n\t\nLease liabilities\n27\n 181 \n 245 \n\t\n\t\nTrade payables\n\t\n\t\n\t\nPayable to micro and small enterprises\n13\n 3 \n 5 \n\t\n\t\n\t\nPayable to other than micro and small enterprises\n13\n 697 \n 1,015 \n\t\n\t\nOther financial liabilities\n13\n 2,491 \n 2,479 \n\t\nOther current liabilities\n14\n 5,903 \n 6,397 \n\t\nEmployee benefit obligations\n15\n 2,052 \n 1,758 \n\t\nIncome tax liabilities (net)\n 182 \n 899 \n 11,509 \n 12,798 \nTOTAL\n 101,350 \n 99,357 \nSummary of material accounting policies\n2\nThe accompanying notes form an integral part of the consolidated financial statements.\nAs per our report of even date\nFor and on behalf of the Board of Directors of \nOracle Financial Services Software Limited \nFor S. R. Batliboi & Associates LLP\nJane Murphy \nMakarand Padalkar \nMrugank Paranjape\nChartered Accountants\nChairperson \nManaging Director \nDirector\nICAI Firm Registration No. 101049W/E300004\nDIN: 08336710 \n& Chief Executive Officer \nDIN: 02162026\nDIN: 02115514  \nper Govind Ahuja\nAvadhut Ketkar \nOnkarnath Banerjee \nPartner\nChief Financial Officer \nCompany Secretary \nMembership No. 048966\n& Compliance Officer \nACS: 8547  \nMumbai, India\nMumbai, India\nApril 25, 2025\nApril 25, 2025\n'

RELIANCE_PAGE_89 = 'As at 31st March, 2024\n(C in crore)\nNotes\nAs at\n31st March, 2024\nAs at\n31st March, 2023\nAssets\nNon-Current Assets\nProperty, Plant and Equipment\n1\n 2,58,911 \n2,52,967\nIntangible Assets\n1\n 40,719 \n29,334\nCapital Work-in-Progress\n1\n 44,294 \n30,972\nIntangible Assets Under Development\n1\n 17,338 \n23,385\nFinancial Assets\nInvestments\n2\n 3,01,400 \n2,65,067\nLoans\n3\n 10,051 \n12,431\nOther Financial Assets\n4\n 16,902 \n2,215\nOther Non-Current Assets\n5\n 7,403 \n2,832\nTotal Non-Current Assets\n6,97,018\n6,19,203\nCurrent Assets\nInventories\n6\n 85,100 \n84,756\nFinancial Assets\nInvestments\n7\n 68,663 \n86,074\nTrade Receivables\n8\n 14,740 \n24,143\nCash and Cash Equivalents\n9\n 69,248 \n61,007\nLoans\n10\n -   \n595\nOther Financial Assets\n11\n 11,747 \n35,109\nOther Current Assets\n13\n 13,127 \n11,773\nTotal Current Assets\n2,62,625\n3,03,457\nTotal Assets\n9,59,643\n9,22,660\n(C in crore)\nNotes\nAs at\n31st March, 2024\nAs at\n31st March, 2023\nEquity and Liabilities\nEquity\nEquity Share capital\n14\n 6,766 \n6,766\nOther Equity\n15\n 5,08,330 \n4,72,312\nTotal Equity\n5,15,096\n4,79,078\nLiabilities\nNon-Current Liabilities\nFinancial Liabilities\nBorrowings\n16\n 1,61,059 \n1,35,561\nLease Liabilities\n 2,692 \n2,786\nOther Financial Liabilities\n17\n -   \n584\nProvisions\n18\n 1,701 \n1,296\nDeferred Tax Liabilities (Net)\n19\n 36,259 \n33,968\nOther Non-Current Liabilities\n20\n 2,822 \n -   \nTotal Non-Current Liabilities\n2,04,533\n1,74,195\nCurrent Liabilities\nFinancial Liabilities\nBorrowings\n21\n 50,731 \n80,262\nLease Liabilities\n 93 \n97\nTrade Payables Due to:\n22\n\t\nMicro and Small Enterprises\n 492 \n533\n\t\nOther than Micro and Small Enterprises\n 1,29,367 \n1,19,278\nOther Financial Liabilities\n23\n 27,493 \n42,468\nOther Current Liabilities\n24\n 30,866 \n25,735\nProvisions\n25\n 972 \n1,014\nTotal Current Liabilities\n2,40,014\n2,69,387\nTotal Liabilities\n4,44,547\n4,43,582\nTotal Equity and Liabilities\n9,59,643\n9,22,660\nMaterial Accounting Policies\nA-C\nSee accompanying Notes to the Financial Statements\n1 to 46\nAs per our Report of even date\nFor Deloitte Haskins & Sells LLP\nChartered Accountants\n(Registration No.  \n117366W/W-100018)\nFor Chaturvedi & Shah LLP\nChartered Accountants\n(Registration No.  \n101720W/W-100355)\nSrikanth Venkatachari\nChief Financial Officer\nAbhijit A. Damle\nPartner  \nMembership No. 102912\nSandesh Ladha\nPartner  \nMembership No. 047841 \nSavithri Parekh\nCompany Secretary\nDate: April 22, 2024 \nFor and on behalf of the Board\nM.D. Ambani\nDIN: 00001695\nN.R. Meswani\t\nH.R. Meswani\nDIN: 00001620\t\nDIN: 00001623\nP.M.S. Prasad\nDIN: 00012144\nAkash M. Ambani\t\nIsha M. Ambani\nDIN: 06984194\t\nDIN: 06984175\nAnant M. Ambani\t\nRaminder Singh Gujral\nDIN: 07945702\t\nDIN: 07175393\nDr. Shumeet Banerji\t\nArundhati Bhattacharya\nDIN: 02787784\t\nDIN: 02011213\nK.V. Chowdary\t\nK.V. Kamath\nDIN: 08485334\t\nDIN: 00043501\nHaigreve Khaitan\nDIN: 00005290\nExecutive Directors\nChairman and \nManaging Director\nNon-Executive  \nDirectors\n130\nReliance Industries Limited\n131\nIntegrated Annual Report 2023-24\n'


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


def _first_whole_word_occurrence(text: str, fragment: str, start: int = 0) -> Optional[int]:
    """
    Independent oracle for the tests below: the position of the first
    genuine whole-word occurrence of `fragment` in `text` at or after
    `start`, using a regex lookaround rather than the production code's own
    hand-rolled retry loop -- so these tests check the fix against a
    differently-implemented definition of "word boundary", not against
    itself. `-` is excluded from `\\w` boundary-safe characters for the same
    reason production code excludes it: a hyphenated compound like
    "non-current assets" must not count as a valid boundary.
    """
    pattern = re.compile(r"(?<![\w-])" + re.escape(fragment) + r"(?![\w-])")
    m = pattern.search(text, start)
    return m.start() if m else None


class TestOrderedFragmentSearchWordBoundary(unittest.TestCase):
    """
    FV2-1: `_ordered_fragment_search`'s plain substring search lets a
    fragment like "current assets" match inside "non-current assets" --
    RC-1's confirmed root cause for OFSS's real total_assets/total_liabilities/
    current_assets/current_liabilities rejections (see
    ROOT_CAUSE_CLASSIFICATION_2026-08-01.md and
    ROOT_CAUSE_CLASSIFICATION_RELIANCE_2026-08-01.md). These tests fail
    against the pre-fix implementation and must pass once the search is
    made boundary-aware (a match is only accepted if the character
    immediately before its start and immediately after its end is neither
    alphanumeric nor a hyphen -- so "non-current assets" no longer counts
    as a valid anchor for "current assets").
    """

    def test_ofss_current_assets_skips_false_anchor_in_non_current_assets(self):
        # Real evidence for OFSS's rejected current_assets candidate
        # (extraction_outputs/3840dcfd02e0/3840dcfd02e0_validator_rejections.csv):
        # evidence = "CURRENT ASSETS\nFinancial assets\n 79,458" -> text
        # fragments ["CURRENT ASSETS", "Financial assets"]. The naive
        # substring search anchors inside "Non-current assets" (the page's
        # first heading); the real "Current assets" heading is further down.
        text_fragments = ["CURRENT ASSETS", "Financial assets"]
        normalized_page = _normalize_for_grounding(OFSS_PAGE_61)
        real_anchor_idx = _first_whole_word_occurrence(normalized_page, "current assets")
        self.assertIsNotNone(real_anchor_idx)
        self.assertGreater(
            real_anchor_idx, normalized_page.find("current assets"),
            "test fixture assumption: at least one invalid (substring) occurrence precedes the real heading",
        )

        result = _ordered_fragment_search(text_fragments, normalized_page)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.matches[0].start, real_anchor_idx,
                          "must skip every false anchor inside a hyphenated compound and land on the real heading")

    def test_ofss_current_liabilities_skips_false_anchor_in_non_current_liabilities(self):
        # Real evidence for OFSS's rejected current_liabilities candidate:
        # evidence = "EQUITY AND LIABILITIES\nCURRENT LIABILITIES\n 11,509"
        # -> text fragments ["EQUITY AND LIABILITIES", "CURRENT LIABILITIES"].
        text_fragments = ["EQUITY AND LIABILITIES", "CURRENT LIABILITIES"]
        normalized_page = _normalize_for_grounding(OFSS_PAGE_61)
        after_first_fragment = normalized_page.find("equity and liabilities") + len("equity and liabilities")
        real_anchor_idx = _first_whole_word_occurrence(normalized_page, "current liabilities", after_first_fragment)
        self.assertIsNotNone(real_anchor_idx)
        self.assertGreater(
            real_anchor_idx, normalized_page.find("current liabilities", after_first_fragment),
            "test fixture assumption: at least one invalid (substring) occurrence precedes the real heading",
        )

        result = _ordered_fragment_search(text_fragments, normalized_page)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.matches[-1].start, real_anchor_idx,
                          "the CURRENT LIABILITIES fragment must land on the real heading, not a false anchor")

    def test_reliance_current_assets_skips_false_anchor_in_non_current_assets(self):
        # RC-1 cross-check on Reliance (ROOT_CAUSE_CLASSIFICATION_RELIANCE_2026-08-01.md,
        # line 39): a bare "Current Assets" evidence fragment -- the same
        # style OFSS's LLM used -- structurally collides with "Non-Current
        # Assets" on this document too, even though Reliance's real live
        # candidate happened to avoid it by quoting "Total Current Assets"
        # instead. This confirms the defect (and its fix) are framework-level,
        # not OFSS-specific.
        text_fragments = ["Current Assets"]
        normalized_page = _normalize_for_grounding(RELIANCE_PAGE_89)
        real_anchor_idx = _first_whole_word_occurrence(normalized_page, "current assets")
        self.assertIsNotNone(real_anchor_idx)
        self.assertGreater(real_anchor_idx, normalized_page.find("current assets"))

        result = _ordered_fragment_search(text_fragments, normalized_page)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.matches[0].start, real_anchor_idx)

    def test_skips_multiple_invalid_occurrences_before_valid_one(self):
        # Generic case: the compound-word collision can recur more than
        # once before a genuine whole-word occurrence exists. The retry
        # loop must not stop after a single skip.
        text_block = _normalize_for_grounding(
            "non-current assets. semi-current assets. Current assets: 500."
        )
        result = _ordered_fragment_search(["current assets"], text_block)
        self.assertTrue(result.succeeded)
        expected_idx = text_block.index("current assets: 500")
        self.assertEqual(result.matches[0].start, expected_idx)

    def test_trailing_boundary_rejects_match_glued_to_following_word(self):
        # Symmetric case on the END of the match: "total" must not match
        # inside "totals" (no separator after the fragment either).
        text_block = _normalize_for_grounding("the totals were reported. total: 500.")
        result = _ordered_fragment_search(["total"], text_block)
        self.assertTrue(result.succeeded)
        expected_idx = text_block.index("total:")
        self.assertEqual(result.matches[0].start, expected_idx)

    def test_no_valid_boundary_occurrence_anywhere_fails(self):
        # If every occurrence of a fragment is glued to other content (no
        # genuine whole-word occurrence exists at all), the search must now
        # correctly fail rather than falsely anchoring on the collision --
        # this is the intended behavior change from the pre-fix version,
        # where a plain substring match would have "succeeded" here.
        text_block = _normalize_for_grounding("non-current assets only, no bare heading exists.")
        result = _ordered_fragment_search(["current assets"], text_block)
        self.assertFalse(result.succeeded)

    def test_duplicate_as_at_fragment_still_matches_distinct_occurrences(self):
        # Regression: the existing duplicate-fragment case
        # (test_duplicate_fragment_matches_distinct_occurrences) must keep
        # working unchanged now that boundary-checking retries are added --
        # both occurrences of "As at" in LICHSGFIN's real column header are
        # already whole-word, so neither should be skipped.
        text_fragments = ["As at", "As at"]
        normalized_page = _normalize_for_grounding(LICHSGFIN_PAGE_217)
        result = _ordered_fragment_search(text_fragments, normalized_page)
        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.matches), 2)
        self.assertNotEqual(result.matches[0].start, result.matches[1].start)


class TestFV2_1KnownDeferredLimitation(unittest.TestCase):
    """
    FV2-1 corrects L4 anchor position but does not close every downstream
    consequence of the frozen "no locality bound on grounded_region" design
    (grounded_region spans [start of FIRST matched fragment, end of LAST
    matched fragment] -- see FRAMEWORK_V2_IMPLEMENTATION_PLAN.md's FV2-1
    entry, "Additional finding", 2026-08-02). This test pins down and
    documents that OFSS's short_term_debt candidate keeps its L4+L5 false
    "pass" even after the word-boundary fix ships, for a different reason
    than before the fix: the corrected anchor jump causes grounded_region to
    balloon across the entire skipped "Non-current liabilities" section,
    which still contains the same decoy value (185) that produced the
    original false accept. This candidate's overall accept/reject status is
    unaffected either way (L2 independently rejects it via raw_label
    "Current liabilities\\nFinancial liabilities" not matching
    short_term_debt's canonical pattern) -- there is no live regression.
    This test exists so nobody mistakes FV2-1 for having closed this risk;
    it is deliberately deferred to be revisited immediately before FV2-3 (see
    FV2-3's entry in the same plan document), not fixed here, per explicit
    scope decision. If this assertion ever starts failing because
    grounded_region's construction changes, that is expected and desirable --
    update/remove this test as part of whatever fix addresses it then.
    """

    def test_short_term_debt_l5_false_pass_persists_after_boundary_fix(self):
        evidence = "EQUITY AND LIABILITIES\nCURRENT LIABILITIES\nFinancial liabilities\n 185"
        l4 = check_evidence_grounding(evidence, OFSS_PAGE_61)
        self.assertTrue(l4.passed, "L4 is expected to pass (correctly anchored) after the boundary fix")
        l5 = check_value_grounding(185.0, l4.grounded_region, OFSS_PAGE_61)
        self.assertTrue(
            l5.passed,
            "documents the known, deferred region-ballooning false-accept "
            "(see class docstring) -- not fixed by FV2-1's scope",
        )


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


class TestEvidenceSelfConfirmsValue(unittest.TestCase):
    """
    FV2-2: the smallest-possible fix for the L5 fixed-window problem.
    Measured live against real OFSS data during implementation (see
    FRAMEWORK_V2_IMPLEMENTATION_PLAN.md's FV2-2 entry): a flat wider
    constant is not safe -- OFSS's current_liabilities needs a 339-char
    window, but LICHSGFIN's frozen total_liabilities fabrication test
    (test_lichsgfin_total_liabilities_cross_row_fabrication_correctly_rejected)
    starts incorrectly passing at 349 chars -- a 10-character margin, too
    fragile to rely on. Instead: every genuine OFSS target (current_assets,
    current_liabilities, long_term_debt) already has its declared value
    quoted verbatim as a "numeric" evidence fragment (excluded from L4's
    text-only search, but present in the raw evidence string) -- while
    every known fabrication (LICHSGFIN's total_liabilities, OFSS's
    hallucinated gross_profit) does NOT have its declared value anywhere
    in its own evidence. This helper is the gate: only self-consistent
    evidence unlocks a wider L5 search.
    """

    def test_ofss_current_assets_evidence_self_confirms(self):
        self.assertTrue(_evidence_self_confirms_value(
            "CURRENT ASSETS\nFinancial assets\n 79,458", 79458.0))

    def test_ofss_current_liabilities_evidence_self_confirms(self):
        self.assertTrue(_evidence_self_confirms_value(
            "EQUITY AND LIABILITIES\nCURRENT LIABILITIES\n 11,509", 11509.0))

    def test_ofss_long_term_debt_evidence_self_confirms(self):
        self.assertTrue(_evidence_self_confirms_value(
            "EQUITY AND LIABILITIES\nNON-CURRENT LIABILITIES\nFinancial liabilities\n 6,217", 6217.0))

    def test_ofss_hallucinated_gross_profit_does_not_self_confirm(self):
        # Real evidence for OFSS's rejected gross_profit candidate: the
        # declared value (259.0) appears nowhere in its own evidence --
        # the only number quoted (33,109) is a completely different
        # figure ("Profit before tax"). This is the negative control that
        # must not unlock a wider search.
        self.assertFalse(_evidence_self_confirms_value("Profit before tax\n33,109", 259.0))

    def test_lichsgfin_total_liabilities_fabrication_does_not_self_confirm(self):
        # Real evidence for LICHSGFIN's frozen fabricated total_liabilities
        # candidate: declared value 291166.89 matches neither number
        # actually quoted in its own evidence (277150.05, 259166.89).
        self.assertFalse(_evidence_self_confirms_value(
            "Total Financial Liabilities\n277150.05\n259166.89", 291166.89))

    def test_within_tolerance_still_counts_as_self_confirmed(self):
        # Reuses the same 2% relative tolerance as the rest of L5, not a
        # stricter exact-match requirement.
        self.assertTrue(_evidence_self_confirms_value("Total Assets\n1000", 1000.0 * 1.0001))

    def test_no_numeric_fragment_in_evidence_does_not_self_confirm(self):
        self.assertFalse(_evidence_self_confirms_value("Total Assets\nsee note 12", 1000.0))


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

    def test_reliance_ppe_false_accept_prevented_for_realistic_multi_fragment_evidence(self):
        # FV2-1's concrete safety benefit, for evidence shaped like real LLM
        # output: ROOT_CAUSE_CLASSIFICATION_RELIANCE_2026-08-01.md (line 39)
        # documents that a "Current Assets" fragment -- pre-fix -- falsely
        # anchors inside "Non-Current Assets" and then L5's 40-char window
        # finds 258,911 (Property, Plant & Equipment's value), within the 2%
        # relative tolerance of the true current_assets value (2,62,625)
        # purely by coincidence (1.43% off) -- a false accept on a completely
        # unrelated number. Every real multi-fragment evidence string
        # observed in this project's benchmarks (OFSS's current_assets rejection
        # included) pairs the heading with at least one following line-item
        # fragment, which is what actually determines grounded_region's
        # content -- so this test uses that same realistic shape ("Current
        # Assets" + "Inventories", the real page's next line) rather than a
        # bare heading alone. See the next test for why a bare heading alone
        # is a separate, NOT-fixed-by-FV2-1 case.
        evidence = "Current Assets\nInventories\n 2,62,625"
        gr = check_evidence_grounding(evidence, RELIANCE_PAGE_89)
        self.assertTrue(gr.passed)
        result = check_value_grounding(262625.0, gr.grounded_region, RELIANCE_PAGE_89)
        self.assertNotEqual(
            result.matched_number, 258911.0,
            "must not false-accept on Property, Plant & Equipment's value",
        )

    def test_reliance_ppe_false_accept_survives_for_bare_single_fragment_evidence(self):
        # Documents a THIRD, pre-existing L5-level defect discovered while
        # verifying FV2-1, independent of L4's anchor and NOT fixed by this
        # change (out of scope: "do not modify L5"). check_value_grounding
        # does not use L4's actual match position -- it re-derives location
        # via its own plain `normalized_text_block.find(grounded_region)` on
        # the bare grounded_region STRING. When grounded_region is short and
        # non-unique (a single bare "current assets" fragment with no
        # following line-item context, exactly RC-13's original simulated
        # example), that re-find lands on the SAME false, ambiguous first
        # occurrence L4 used to anchor on before this fix -- regardless of
        # where L4 itself now correctly anchors. This is orthogonal to FV2-1
        # and was already true before it; FV2-1 only closes the false accept
        # for evidence whose grounded_region is unique on the page (the
        # realistic multi-fragment case above).
        evidence = "Current Assets\n 2,62,625"
        gr = check_evidence_grounding(evidence, RELIANCE_PAGE_89)
        self.assertTrue(gr.passed)
        self.assertEqual(gr.grounded_region, "current assets")
        result = check_value_grounding(262625.0, gr.grounded_region, RELIANCE_PAGE_89)
        self.assertEqual(
            result.matched_number, 258911.0,
            "documents the known, deferred L5-level false accept (see docstring) -- not fixed by FV2-1's scope",
        )

    def test_reliance_ppe_false_accept_unaffected_by_fv2_2_self_confirm(self):
        # Confirms FV2-2 does not change the outcome of the pre-existing
        # defect above even when evidence IS passed and IS self-confirming
        # (2,62,625 is quoted verbatim, so _evidence_self_confirms_value
        # would be True): the widened search still starts from the same
        # wrong region_pos (99, inside "Non-Current Assets" -- see the
        # class docstring above), so 258,911 is still the first number
        # encountered regardless of how far the search now extends. FV2-2
        # neither fixes nor worsens this known, separately-deferred issue.
        evidence = "Current Assets\n 2,62,625"
        gr = check_evidence_grounding(evidence, RELIANCE_PAGE_89)
        result = check_value_grounding(262625.0, gr.grounded_region, RELIANCE_PAGE_89, evidence)
        self.assertEqual(result.matched_number, 258911.0)

    def test_fv2_2_ofss_current_assets_now_passes(self):
        # The real, previously-rejected OFSS current_assets candidate
        # (extraction_outputs/3840dcfd02e0/3840dcfd02e0_validator_rejections.csv):
        # L4 already anchors correctly (post-FV2-1); the true subtotal sits
        # 215 measured characters past the anchor -- outside the original
        # 40-char window. Its declared value is quoted verbatim in its own
        # evidence, so FV2-2's self-confirm gate should unlock a wide enough
        # search to find it for real.
        evidence = "CURRENT ASSETS\nFinancial assets\n 79,458"
        gr = check_evidence_grounding(evidence, OFSS_PAGE_61)
        self.assertTrue(gr.passed)
        result = check_value_grounding(79458.0, gr.grounded_region, OFSS_PAGE_61, evidence)
        self.assertTrue(result.passed)
        self.assertEqual(result.matched_number, 79458.0)
        self.assertFalse(result.degraded)

    def test_fv2_2_ofss_current_liabilities_now_passes(self):
        evidence = "EQUITY AND LIABILITIES\nCURRENT LIABILITIES\n 11,509"
        gr = check_evidence_grounding(evidence, OFSS_PAGE_61)
        self.assertTrue(gr.passed)
        result = check_value_grounding(11509.0, gr.grounded_region, OFSS_PAGE_61, evidence)
        self.assertTrue(result.passed)
        self.assertEqual(result.matched_number, 11509.0)

    def test_fv2_2_ofss_long_term_debt_now_passes(self):
        # L5-level fix only -- this candidate stays rejected overall
        # because L2 independently fails for it (no canonical pattern for
        # this filer's "Financial liabilities" phrasing), unaffected by
        # FV2-2. Tested here at the L5 layer only.
        evidence = "EQUITY AND LIABILITIES\nNON-CURRENT LIABILITIES\nFinancial liabilities\n 6,217"
        gr = check_evidence_grounding(evidence, OFSS_PAGE_61)
        self.assertTrue(gr.passed)
        result = check_value_grounding(6217.0, gr.grounded_region, OFSS_PAGE_61, evidence)
        self.assertTrue(result.passed)
        self.assertEqual(result.matched_number, 6217.0)

    def test_fv2_2_ofss_hallucinated_gross_profit_stays_rejected(self):
        # Negative control: evidence's own number (33,109) doesn't match
        # the declared value (259.0) at all, so the self-confirm gate never
        # fires and behavior is identical to before FV2-2 -- correctly
        # rejected, not swept up by a wide search.
        evidence = "Profit before tax\n33,109"
        gr = check_evidence_grounding(evidence, OFSS_PAGE_61)
        result = check_value_grounding(259.0, gr.grounded_region, OFSS_PAGE_61, evidence)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "value_not_grounded")

    def test_fv2_2_lichsgfin_total_liabilities_fabrication_stays_rejected(self):
        # The frozen fabrication case
        # (test_lichsgfin_total_liabilities_cross_row_fabrication_correctly_rejected
        # below): a flat width increase to 349+ chars would have broken
        # this test (measured live during FV2-2's investigation -- only 10
        # chars above the 339 chars OFSS's current_liabilities needs). The
        # self-confirm gate protects it structurally instead: this
        # candidate's own evidence doesn't quote its declared value at all,
        # so it never gets the wider search, regardless of any width
        # constant.
        evidence = "Total Financial Liabilities\n277150.05\n259166.89"
        gr = check_evidence_grounding(evidence, LICHSGFIN_PAGE_217)
        result = check_value_grounding(291166.89, gr.grounded_region, LICHSGFIN_PAGE_217, evidence)
        self.assertFalse(result.passed)

    def test_fv2_2_self_confirmed_value_genuinely_absent_still_rejected(self):
        # Synthetic edge case (not real benchmark data): even when evidence
        # is internally self-consistent with the declared value, that value
        # must still actually be found somewhere in text_block. Being
        # self-confirming only unlocks a WIDER search -- it is not treated
        # as grounding by itself.
        text_block = "Total Assets\nSome Label\n999\nOther Label\n888"
        evidence = "Total Assets\n12345"
        gr = check_evidence_grounding(evidence, text_block)
        self.assertTrue(gr.passed)
        result = check_value_grounding(12345.0, gr.grounded_region, text_block, evidence)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "value_not_grounded")

    def test_fv2_2_does_not_change_result_for_already_passing_candidate(self):
        # Monotonicity check: a candidate whose value is already found
        # within the original 40-char window gets an identical result
        # whether or not a self-confirming evidence string is supplied --
        # the wider search space is always a superset starting at the same
        # position, and the first-match-wins loop returns the same match
        # either way.
        evidence = "Total Assets\n25,095.53 \n24,473.83"
        gr = check_evidence_grounding(evidence, JIOFIN_PAGE_83)
        without_evidence = check_value_grounding(25095.53, gr.grounded_region, JIOFIN_PAGE_83)
        with_evidence = check_value_grounding(25095.53, gr.grounded_region, JIOFIN_PAGE_83, evidence)
        self.assertEqual(without_evidence.passed, with_evidence.passed)
        self.assertEqual(without_evidence.matched_number, with_evidence.matched_number)
        self.assertEqual(without_evidence.reason, with_evidence.reason)


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

    def test_fv2_2_ofss_current_assets_now_fully_accepted(self):
        # End-to-end regression for FV2-2's one real, live benchmark impact:
        # the real OFSS current_assets candidate (raw_label already passes
        # L2, per its persisted rejection_reason having no
        # "raw_label_mismatch" component) was rejected purely on
        # value_not_grounded before FV2-2 and must now be fully accepted.
        candidate = _make_candidate(
            "current_assets", "Current assets", 79458.0,
            "CURRENT ASSETS\nFinancial assets\n 79,458", statement_type="balance_sheet",
        )
        accepted, diagnostics = validate_candidates([candidate], OFSS_PAGE_61)
        self.assertEqual(accepted, [candidate])
        d = diagnostics[0]
        self.assertTrue(d.l2_passed)
        self.assertTrue(d.l4_passed)
        self.assertTrue(d.l5_passed)
        self.assertEqual(d.rejection_reasons, [])

    def test_fv2_2_ofss_current_liabilities_now_fully_accepted(self):
        candidate = _make_candidate(
            "current_liabilities", "Current liabilities", 11509.0,
            "EQUITY AND LIABILITIES\nCURRENT LIABILITIES\n 11,509", statement_type="balance_sheet",
        )
        accepted, diagnostics = validate_candidates([candidate], OFSS_PAGE_61)
        self.assertEqual(accepted, [candidate])
        d = diagnostics[0]
        self.assertTrue(d.l2_passed)
        self.assertTrue(d.l4_passed)
        self.assertTrue(d.l5_passed)
        self.assertEqual(d.rejection_reasons, [])

    def test_fv2_2_ofss_total_liabilities_stays_rejected_l2_blocked(self):
        # Confirms Finding 2 (FV2-1's report) still holds after FV2-2: this
        # candidate's raw_label "TOTAL" fails L2 independently, so it stays
        # rejected overall even though FV2-2 now makes L5 pass on it (the
        # self-confirm gate fires here too, since 101,350 is quoted
        # verbatim -- but that number is genuinely wrong for
        # total_liabilities, per Finding 2). No live behavior change; L2
        # remains the reason this candidate is excluded from FV2-1/FV2-2's
        # claimed benefit.
        candidate = _make_candidate(
            "total_liabilities", "TOTAL", 101350.0,
            "EQUITY AND LIABILITIES\nTOTAL\n 101,350 \n 99,357", statement_type="balance_sheet",
        )
        accepted, diagnostics = validate_candidates([candidate], OFSS_PAGE_61)
        self.assertEqual(accepted, [])
        d = diagnostics[0]
        self.assertFalse(d.l2_passed)

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


# ---------------------------------------------------------------------------
# _normalize_evidence_layout() -- cash-flow evidence-format fix
#
# Real page text captured from extraction.parser.parse_pdf(), same style as
# the fixtures above. Sourced from the exact pages the Benchmark V2 rejected
# cash_flow candidates came from (TataSteel p.286, LICHSGFIN p.339/p.340).
# ---------------------------------------------------------------------------

TATASTEEL_PAGE_286_CASHFLOW = (
    'Net cash from/(used in) operating activities\n23,879.91\n27,324.93\n(B)\n'
    'Cash flows from investing activities:\n\t\nPurchase of capital assets\n'
    '(11,105.71)\n(10,876.23)\nSale of capital assets\n25.28\n221.14\n'
    'Advance received against sale of property, plant and equipment\n750.00\n-\n'
    'Purchase of investments in subsidiaries*\n(24,575.72)\n(176.41)\n'
    'Purchase of other non-current investments\n(327.73)\n(0.01)\nSale of inve'
)

LICHSGFIN_PAGE_339_CASHFLOW = (
    'Net Cash used in Operations\n5,569.74\n7,212.64\n'
    'Loans Disbursed (Net of repayments)\n(22,178.91)\n(14,621.14)\n'
    'Asset held for sale\n-\n257.09\n\t\n'
    'Net Cash (Used in) Operating Activities (A)\n(16,609.17)\n(7,151.41)\n'
    'B. Cash Flow from Investing Activities\n'
    'Payments for Property, Plant and Equipment\n(73.35)\n(37.45)\n'
    'Proceeds from Sale of Property, Plant and Equipment\n0.10\n0.80\n'
    'Payments for Purchase of Investments\n(981.97)\n(88.43)\n'
    'Proceeds from Sale of Investments\n115.92\n711.59\n'
    'Dividends Received\n0.52\n0.43\nIncrease in Minority\n0.24\n0.35\n\t\n'
    'Net Cash Inflow/ (used in) Investing Activities (B)\n(938.54)\n587.29\n'
    'C. Cash Flow from Financing Activities\nProceeds from B'
)

LICHSGFIN_PAGE_340_CASHFLOW = (
    'Net Cash generated from Financing Activities (C)\n17,413.47\n7,345.57\n'
    '\t\x07Effect of exchange differences on translation of foreign currency cash and cash \n'
    'equivalents\n-\n(0.01)\n\t\n'
)


class TestNormalizeEvidenceLayout(unittest.TestCase):
    """
    Pure transformation tests for _normalize_evidence_layout(), using the
    real rejected-candidate evidence strings pulled from the last
    benchmark's validator_rejections.csv files, plus the two adversarial
    constructions probed during the design review (fiscal-year range,
    glued reference code).
    """

    def test_tatasteel_operating_cash_flow_colon_fused(self):
        evidence = 'Net cash from/(used in) operating activities: 23,879.91'
        self.assertEqual(
            _normalize_evidence_layout(evidence),
            'Net cash from/(used in) operating activities\n23,879.91',
        )

    def test_tatasteel_operating_cash_flow_second_year(self):
        evidence = 'Net cash from/(used in) operating activities: (2,314.03), 27,324.93'
        self.assertEqual(
            _normalize_evidence_layout(evidence),
            'Net cash from/(used in) operating activities\n(2,314.03), 27,324.93',
        )

    def test_lichsgfin_investing_space_fused(self):
        evidence = 'Net Cash Inflow/ (used in) Investing Activities (B) (73.35) (37.45)'
        self.assertEqual(
            _normalize_evidence_layout(evidence),
            'Net Cash Inflow/ (used in) Investing Activities (B)\n(73.35) (37.45)',
        )

    def test_lichsgfin_operating_fused_with_fabricated_note_ref(self):
        # The "(A)" here is fabricated by the model -- real LICHSGFIN p.339
        # text never follows "Net Cash used in Operations" with "(A)" (that
        # suffix belongs to a different, later line). Normalization still
        # splits the layout correctly; whether the resulting label is
        # actually grounded is L4's job -- see the integration test below,
        # which confirms this candidate still correctly fails.
        evidence = 'Net Cash used in Operations (A) 5,569.74 7,212.64'
        self.assertEqual(
            _normalize_evidence_layout(evidence),
            'Net Cash used in Operations (A)\n5,569.74 7,212.64',
        )

    def test_lichsgfin_financing_regression_split_point(self):
        # Already passes L4 today as one glued fragment (Benchmark V2
        # rejection reason for this candidate was only "raw_label_mismatch",
        # no L4 failure). Confirms normalization finds the correct boundary
        # rather than corrupting an already-working case.
        evidence = 'Net Cash generated from Financing Activities (C) 17,413.47 7,345.57'
        self.assertEqual(
            _normalize_evidence_layout(evidence),
            'Net Cash generated from Financing Activities (C)\n17,413.47 7,345.57',
        )

    def test_reliance_empty_evidence_is_untouched(self):
        self.assertEqual(_normalize_evidence_layout(""), "")

    def test_fiscal_year_range_not_misread_as_negative_number(self):
        # Adversarial case from the design review: "2023-24" must not be
        # split at the hyphen (misreading "-24" as a negative number) or
        # have "24" absorbed into the trailing numeric run.
        evidence = "Net cash from operating activities 2023-24 23,879.91"
        self.assertEqual(
            _normalize_evidence_layout(evidence),
            "Net cash from operating activities 2023-24\n23,879.91",
        )

    def test_f95_reference_code_not_absorbed_into_trailing_run(self):
        # Adversarial case from the design review: a short reference code
        # glued to a letter ("F95") must never be treated as a trailing
        # value, nor pull the run leftward and break "F95" apart.
        evidence = "Revenue from operations 24 F95 1,32,516.66"
        self.assertEqual(
            _normalize_evidence_layout(evidence),
            "Revenue from operations 24 F95\n1,32,516.66",
        )

    def test_already_newline_separated_is_untouched(self):
        evidence = "Total Assets\n25,095.53 \n24,473.83"
        self.assertEqual(_normalize_evidence_layout(evidence), evidence)

    def test_bare_reference_number_without_decimal_or_comma_does_not_trigger(self):
        # "24" alone has no '.'/',' -- excluded by the financial-figure
        # safeguard rather than treated as a fused value.
        evidence = "Depreciation and amortisation expense note 24"
        self.assertEqual(_normalize_evidence_layout(evidence), evidence)

    def test_run_spanning_entire_string_does_not_trigger(self):
        evidence = "23,879.91"
        self.assertEqual(_normalize_evidence_layout(evidence), evidence)

    def test_numeric_only_prefix_with_no_alpha_does_not_trigger(self):
        evidence = "*** 1,234.56"
        self.assertEqual(_normalize_evidence_layout(evidence), evidence)

    def test_idempotent_on_already_normalized_output(self):
        cases = (
            'Net cash from/(used in) operating activities: 23,879.91',
            'Net cash from/(used in) operating activities: (2,314.03), 27,324.93',
            'Net Cash Inflow/ (used in) Investing Activities (B) (73.35) (37.45)',
            'Net Cash used in Operations (A) 5,569.74 7,212.64',
            'Net Cash generated from Financing Activities (C) 17,413.47 7,345.57',
            "Net cash from operating activities 2023-24 23,879.91",
            "Revenue from operations 24 F95 1,32,516.66",
        )
        for evidence in cases:
            once = _normalize_evidence_layout(evidence)
            twice = _normalize_evidence_layout(once)
            self.assertEqual(once, twice, msg=f"not idempotent for: {evidence!r}")


class TestCashFlowEvidenceFixIntegration(unittest.TestCase):
    """
    End-to-end checks against real page text (via check_evidence_grounding /
    check_value_grounding, and validate_candidates for one full-pipeline
    case) confirming what the normalization fix actually changes for the
    exact candidates rejected in the last Benchmark V2 run -- and, just as
    importantly, what it correctly leaves rejected.
    """

    def test_tatasteel_operating_cash_flow_now_grounds(self):
        evidence = 'Net cash from/(used in) operating activities: 23,879.91'
        l4 = check_evidence_grounding(evidence, TATASTEEL_PAGE_286_CASHFLOW)
        self.assertTrue(l4.passed, msg=l4.reason)
        l5 = check_value_grounding(23879.91, l4.grounded_region, TATASTEEL_PAGE_286_CASHFLOW)
        self.assertTrue(l5.passed)
        self.assertFalse(l5.degraded)

    def test_tatasteel_operating_cash_flow_second_year_now_grounds(self):
        # The evidence's own embedded number, (2,314.03), is not the
        # declared value and doesn't appear near this label in the real
        # source -- L5 must find 27,324.93 from the real text_block, not
        # trust the evidence string's own numbers.
        evidence = 'Net cash from/(used in) operating activities: (2,314.03), 27,324.93'
        l4 = check_evidence_grounding(evidence, TATASTEEL_PAGE_286_CASHFLOW)
        self.assertTrue(l4.passed, msg=l4.reason)
        l5 = check_value_grounding(27324.93, l4.grounded_region, TATASTEEL_PAGE_286_CASHFLOW)
        self.assertTrue(l5.passed)
        self.assertFalse(l5.degraded)

    def test_lichsgfin_investing_now_grounds_despite_wrong_evidence_numbers(self):
        # Evidence's own numbers (73.35 / 37.45) are wrong relative to the
        # real source (938.54 / 587.29) -- the clearest demonstration of
        # the fix: L4 only needs the label; L5 re-derives the value from
        # the real text_block independently of what the evidence claimed.
        evidence = 'Net Cash Inflow/ (used in) Investing Activities (B) (73.35) (37.45)'
        l4 = check_evidence_grounding(evidence, LICHSGFIN_PAGE_339_CASHFLOW)
        self.assertTrue(l4.passed, msg=l4.reason)
        l5 = check_value_grounding(-938.54, l4.grounded_region, LICHSGFIN_PAGE_339_CASHFLOW)
        self.assertTrue(l5.passed)
        self.assertFalse(l5.degraded)
        self.assertEqual(l5.matched_number, -938.54)

    def test_lichsgfin_operating_still_fails_fabricated_note_ref(self):
        # "(A)" is fabricated by the model -- the real source line is
        # "Net Cash used in Operations" with no note reference (that suffix
        # belongs to a different, later line: "Net Cash (Used in) Operating
        # Activities (A)"). Normalization must not manufacture a false
        # accept: L4 should still correctly fail.
        evidence = 'Net Cash used in Operations (A) 5,569.74 7,212.64'
        l4 = check_evidence_grounding(evidence, LICHSGFIN_PAGE_339_CASHFLOW)
        self.assertFalse(l4.passed)
        self.assertIn("fragment_not_found_in_order", l4.reason)

    def test_lichsgfin_financing_regression_still_grounds(self):
        evidence = 'Net Cash generated from Financing Activities (C) 17,413.47 7,345.57'
        l4 = check_evidence_grounding(evidence, LICHSGFIN_PAGE_340_CASHFLOW)
        self.assertTrue(l4.passed, msg=l4.reason)
        l5 = check_value_grounding(17413.47, l4.grounded_region, LICHSGFIN_PAGE_340_CASHFLOW)
        self.assertTrue(l5.passed)
        self.assertFalse(l5.degraded)

    def test_reliance_empty_evidence_still_fails_no_text_anchor(self):
        l4 = check_evidence_grounding("", TATASTEEL_PAGE_286_CASHFLOW)
        self.assertFalse(l4.passed)
        self.assertEqual(l4.reason, "no_text_anchor_in_evidence")

    def test_full_pipeline_accepts_rescued_lichsgfin_investing_candidate(self):
        candidate = _make_candidate(
            "investing_cash_flow",
            "Net Cash Inflow/ (used in) Investing Activities (B)",
            -938.54,
            'Net Cash Inflow/ (used in) Investing Activities (B) (73.35) (37.45)',
            statement_type="cash_flow", doc_id="287f1ff9bd7d", year=2025,
        )
        _, diagnostics = validate_candidates([candidate], LICHSGFIN_PAGE_339_CASHFLOW)
        self.assertEqual(len(diagnostics), 1)
        # Only assert on what this fix changed (L4/L5) -- L2 depends on
        # canonical_metrics.match_metric()'s own patterns for
        # investing_cash_flow, which are unrelated to and unmodified by
        # this fix (a separate, already-documented Benchmark V2 issue).
        self.assertTrue(diagnostics[0].l4_passed)
        self.assertTrue(diagnostics[0].l5_passed)


if __name__ == "__main__":
    unittest.main()
