
❯ /rename llm_test 
  ⎿  Session renamed to: llm_test

❯ I want to create a proof-of-concept LLM extraction experiment.                
                                                                                
  Do not use Ollama or local models yet.                                        
                                                                                
  Instead:                                                                      
                                                                                
  1. Use PyMuPDF to extract text from the Oracle Financial Services Software    
  annual report.                                                                
  2. Use a hosted LLM API (OpenRouter preferred).                               
  3. Evaluate Qwen2.5-Instruct for financial metric extraction.                 
  4. Extract:                                                                   
                                                                                
     * revenue                                                                  
     * net profit                                                               
     * total assets                                                             
     * total equity                                                             
     * total debt                                                               
     * current assets                                                           
     * current liabilities                                                      
     * operating cash flow                                                      
     and other mentioned metrics in the files                                   
  5. Return structured JSON.                                                    
  6. Include confidence scores.                                                 
  7. Include source evidence snippets.                                          
  8. Measure latency and extraction coverage.                                   
                                                                                
  Do not modify the existing architecture.                                      
  This is an isolated feasibility experiment only.                              
                                                                                

⏺ Let me survey the project structure and find the annual report files before
  writing anything.

  Searched for 2 patterns, read 2 files (ctrl+o to expand)

⏺ The .env has a placeholder Anthropic key, and the OFSS report is at
  uploads/AR_26628_OFSS_2024_2025_A_27062025192147.pdf. I need an OpenRouter API
  key from you before I can write the experiment. Let me also check what
  packages are available.

⏺ Bash(pip show pymupdf openai requests 2>/dev/null | grep -E "^(Name|Version)")
  ⎿  Name: PyMuPDF
     Version: 1.26.5
     Name: openai
     … +3 lines (ctrl+o to expand)

  Read 1 file (ctrl+o to expand)

⏺ Good — PyMuPDF and the openai SDK (which works with OpenRouter's compatible
  API) are both installed. I just need your OpenRouter API key to wire up the
  experiment. Do you have one?

⏺ User declined to answer questions
  ⎿  · What is your OpenRouter API key? (It will only be used in the experiment 
     script, stored in a local .env variable, never committed.) (I'll paste it 
     in chat / It's already in .env as OPENROUTER_API_KEY / Use Anthropic key 
     instead (already in .env))

⏺ What would you like to clarify about the API key or the experiment setup?

✻ Cooked for 52s

❯ i havent added or created the api key yet ,                                   

⏺ No problem. Here's how to get one quickly:

  Steps to get an OpenRouter API key:

  1. Go to openrouter.ai and sign up (Google login works)
  2. Once logged in, go to Keys → Create Key
  3. Give it a name like fintool-poc
  4. Copy the key (starts with sk-or-...)
  5. Add some credits — even $1–2 is more than enough for this experiment
  (Qwen2.5-7B costs ~$0.07/M tokens)

  Then add it to your .env file:

  OPENROUTER_API_KEY=sk-or-v1-your-key-here

  Once you've done that, come back and I'll write the full experiment script. It
  will:
  - Read OPENROUTER_API_KEY from .env
  - Extract text from the OFSS PDF via PyMuPDF
  - Send chunks to qwen/qwen-2.5-7b-instruct on OpenRouter
  - Return structured JSON with confidence scores, evidence snippets, latency,
  and coverage

  The whole experiment lives in one self-contained file
  (extraction/llm_poc_experiment.py) and touches nothing in the existing
  codebase.

✻ Baked for 13s

※ recap: Building an isolated LLM extraction proof-of-concept for the OFSS annual report using PyMuPDF and Qwen2.5 via OpenRouter. Waiting for you to create an OpenRouter account, generate an API key, and add it to the .env file. (disable recaps in /config)

❯ umm drop that ; Before implementing Extraction V2, design a minimal           
  proof-of-concept experiment.                                                  
                                                                                
  Context:                                                                      
                                                                                
  * Current system uses parser.py, classifier.py, metric_extractor.py,          
  canonical_metrics.py.                                                         
  * We are considering introducing an LLM-based extraction layer.               
  * We have an Oracle Financial Services Software annual report available for   
  testing.                                                                      
  * Hardware: MacBook Air M1, 8GB RAM.                                          
  * Planned model: Qwen2.5:3b via Ollama.                                       
                                                                                
  Tasks:                                                                        
                                                                                
  1. Design the smallest possible experiment to validate LLM-based extraction.  
  2. Explain exactly what text should be sent to the model.                     
  3. Recommend which pages/sections of the annual report should be tested       
  first.                                                                        
  4. Define a JSON schema for extracted metrics.                                
  5. Define evaluation criteria:                                                
                                                                                
     * extraction coverage                                                      
     * accuracy                                                                 
     * latency                                                                  
     * compatibility with numerical_module.py                                   
  6. Identify potential risks of using a 3B model.                              
  7. Propose prompt engineering strategies to improve extraction quality.       
                                                                                
  Output:                                                                       
                                                                                
  * Experiment design                                                           
  * Test plan                                                                   
  * Expected results                                                            
  * Go/No-Go criteria for migrating to Extraction V2                            
                                                                                
  Do not implement code yet.                                                    
                                                                                
  ⎿  ⧉ Selected 33 lines from file.txt in Visual Studio Code

⏺ PoC Experiment Design: LLM-Based Extraction with Qwen2.5:3b

  ---
  1. Experiment Scope (Minimum Viable Test)

  The goal is to answer one binary question: can a 3b LLM reliably extract 
  structured financial metrics from Indian annual report text, with values that 
  match ground truth within 1% tolerance?

  Keep the experiment to one document, three statement types, one model call per
  statement page. Nothing more.

  OFSS Annual Report
        ↓
  PyMuPDF (text extraction — already works)
        ↓
  Page filter (balance sheet + P&L + cash flow pages only)
        ↓
  Qwen2.5:3b via Ollama
        ↓
  Structured JSON
        ↓
  Compare against manually verified ground truth

  ---
  2. What Text to Send to the Model

  Do NOT send the whole PDF. The 3b model has a limited effective context, and
  wall-of-text input degrades accuracy sharply.

  Send per-statement-page blocks:

  ┌────────────────┬────────────────────────────────────────────────────────┐
  │      What      │                         Detail                         │
  ├────────────────┼────────────────────────────────────────────────────────┤
  │ Unit of input  │ One financial statement page at a time                 │
  ├────────────────┼────────────────────────────────────────────────────────┤
  │ Preprocessing  │ Strip page numbers, running headers, watermarks. Keep  │
  │                │ all label-value pairs intact.                          │
  ├────────────────┼────────────────────────────────────────────────────────┤
  │ Max input      │ ~1500–2000 tokens per call (roughly 1–2 dense table    │
  │ length         │ pages)                                                 │
  ├────────────────┼────────────────────────────────────────────────────────┤
  │ Format         │ Raw PyMuPDF text, minimal cleanup — preserve alignment │
  │                │  cues like spaces and newlines                         │
  ├────────────────┼────────────────────────────────────────────────────────┤
  │ Do NOT include │ MDA text, auditor notes, director bios — these add     │
  │                │ noise with no value                                    │
  └────────────────┴────────────────────────────────────────────────────────┘

  If a single statement spans multiple pages (common in Indian ARs), concatenate
  them into one call only if combined tokens stay under 2000. Otherwise split
  by page and merge results afterward.

  ---
  3. Recommended Page Order

  Test in this order — easiest to hardest for the model:

  ┌───────┬──────────────────┬──────────────────────────────────────────────┐
  │ Order │    Statement     │                Why This Order                │
  ├───────┼──────────────────┼──────────────────────────────────────────────┤
  │ 1st   │ Balance Sheet    │ Most structured, clearest labels,            │
  │       │                  │ column-aligned values                        │
  ├───────┼──────────────────┼──────────────────────────────────────────────┤
  │ 2nd   │ Income Statement │ Slightly more narrative but still tabular    │
  │       │  (P&L)           │                                              │
  ├───────┼──────────────────┼──────────────────────────────────────────────┤
  │ 3rd   │ Cash Flow        │ Indirect method format is harder; subtotals  │
  │       │ Statement        │ can confuse small models                     │
  └───────┴──────────────────┴──────────────────────────────────────────────┘

  Start with consolidated statements only. Standalone can be tested afterward if
  consolidated works.

  ---
  4. JSON Schema for Extracted Metrics

  {
    "document_id": "string",
    "model": "qwen2.5:3b",
    "extraction_timestamp": "ISO-8601",
    "metrics": [
      {
        "metric_name": "revenue",
        "canonical_name": "revenue",
        "raw_label": "Income from Operations",
        "value": 189483.0,
        "unit": "INR Crore",
        "year": 2024,
        "confidence": 0.92,
        "evidence_snippet": "Income from Operations  1,89,483  1,72,914",
        "page_no": 87,
        "statement_type": "income_statement",
        "section_type": "consolidated"
      }
    ],
    "extraction_stats": {
      "latency_seconds": 18.4,
      "prompt_tokens": 1420,
      "completion_tokens": 380,
      "metrics_found": 9,
      "metrics_missing": ["total_debt", "interest_expense"]
    }
  }

  The canonical_name field must use exactly the names from canonical_metrics.py
  — this is what downstream numerical_module.py expects.

  ---
  5. Evaluation Criteria

  A. Extraction Coverage
  - Count: how many of the 17 canonical metrics were found vs total
  - Target: ≥ 10/17 (59%) to justify Extraction V2
  - Current system baseline from the OFSS benchmark: compare directly

  B. Accuracy
  - For each extracted value, compare against manually verified ground truth
  from the PDF
  - Tolerance: ±1% for numeric values (accounts for rounding/unit scaling)
  - Flag: any value that is off by >5% is a hallucination risk

  C. Latency
  - Measure: wall-clock seconds from Ollama call start to JSON response received
  - Target: <45 seconds per statement page on M1 8GB
  - If >90 seconds per page, 3b is too slow for practical use

  D. Compatibility with numerical_module.py
  - The extracted canonical_name values must map cleanly to the
  resolved_metrics_df column contract
  - Required columns: doc_id | metric_name | value | unit | year | page_no | 
  raw_label | statement_type | section_type | confidence | source
  - Test: feed LLM output directly into build_resolved_metrics_df() with zero
  manual intervention

  ---
  6. Risks of Using a 3B Model

  ┌────────────────────┬────────────┬───────────────────────────────────────┐
  │        Risk        │  Severity  │              Description              │
  ├────────────────────┼────────────┼───────────────────────────────────────┤
  │                    │            │ Model may fabricate plausible-looking │
  │ Hallucination      │ High       │  numbers not present in text. Most    │
  │                    │            │ dangerous risk.                       │
  ├────────────────────┼────────────┼───────────────────────────────────────┤
  │                    │            │ 3b models frequently break JSON       │
  │ JSON malformation  │ Medium     │ structure, miss closing brackets, or  │
  │                    │            │ truncate.                             │
  ├────────────────────┼────────────┼───────────────────────────────────────┤
  │                    │            │ 1,89,483 (Indian) vs 189,483          │
  │ Indian number      │ Medium     │ (Western) — model may parse           │
  │ format             │            │ incorrectly or silently drop the      │
  │                    │            │ comma convention                      │
  ├────────────────────┼────────────┼───────────────────────────────────────┤
  │ Standalone vs      │            │ If both appear on the same page, the  │
  │ consolidated       │ Medium     │ model may mix values from both        │
  │ confusion          │            │                                       │
  ├────────────────────┼────────────┼───────────────────────────────────────┤
  │ Multi-year column  │            │ Table has 2024 and 2023 columns —     │
  │ confusion          │ Medium     │ model may assign values to the wrong  │
  │                    │            │ year                                  │
  ├────────────────────┼────────────┼───────────────────────────────────────┤
  │ Context window     │ Low-Medium │ At 2000+ tokens, 3b models lose       │
  │ degradation        │            │ coherence on structured tables        │
  ├────────────────────┼────────────┼───────────────────────────────────────┤
  │ Instruction        │            │ Larger models follow schema prompts   │
  │ following          │ Low        │ reliably; 3b may ignore field         │
  │                    │            │ constraints                           │
  └────────────────────┴────────────┴───────────────────────────────────────┘

  ---
  7. Prompt Engineering Strategies

  A. System Prompt — Lock the Role and Format
  You are a financial data extraction engine.
  Extract ONLY values explicitly present in the provided text.
  Do NOT calculate, infer, or estimate.
  Output ONLY valid JSON. No explanation.

  B. One Statement Per Call — Never Mix
  Each LLM call receives exactly one statement type. Never send balance sheet +
  P&L together — the model will cross-contaminate values.

  C. Explicit Schema in Prompt
  Include the target JSON structure directly in the prompt with field names and
  types. Small models need this anchor.

  D. Temperature = 0
  Financial extraction is deterministic. No creativity needed. Temperature = 0
  gives consistent output.

  E. Explicit Year Anchoring
  Tell the model: "The column headers show years. Map each value to its correct 
  year column. The current year is the left-most data column."

  F. Number Format Warning
  Include: "Numbers may use Indian formatting: 1,89,483 means 189483. Convert to
  plain integers."

  G. Chain-of-Thought for Confidence Scores
  Ask the model to rate confidence per metric: "If the label closely matches a 
  known metric name and the value is unambiguous, confidence = high. If inferred
  or ambiguous, confidence = low."

  H. Constrain Canonical Names
  Pass the 17 canonical names directly in the prompt: "Map each extracted metric
  to one of these canonical names ONLY: [revenue, net_profit, total_assets, 
  ...]". This prevents free-form labels from leaking into downstream modules.

  ---
  Expected Results

  ┌──────────────────────┬────────────┬───────────┬─────────────┐
  │        Metric        │ Optimistic │ Realistic │ Pessimistic │
  ├──────────────────────┼────────────┼───────────┼─────────────┤
  │ Coverage (out of 17) │ 12         │ 8–10      │ 4–6         │
  ├──────────────────────┼────────────┼───────────┼─────────────┤
  │ Accuracy (within 1%) │ 90%        │ 70–80%    │ <60%        │
  ├──────────────────────┼────────────┼───────────┼─────────────┤
  │ Latency per page     │ 20s        │ 35–45s    │ >60s        │
  ├──────────────────────┼────────────┼───────────┼─────────────┤
  │ JSON validity rate   │ 95%        │ 80%       │ <70%        │
  ├──────────────────────┼────────────┼───────────┼─────────────┤
  │ Hallucination rate   │ <5%        │ 10–15%    │ >20%        │
  └──────────────────────┴────────────┴───────────┴─────────────┘

  ---
  Go / No-Go Criteria for Extraction V2

  ┌────────────────────┬───────────────────────────────────┬────────────────┐
  │     Criterion      │                Go                 │     No-Go      │
  ├────────────────────┼───────────────────────────────────┼────────────────┤
  │ Coverage           │ ≥ 10/17 canonical metrics         │ < 8/17         │
  ├────────────────────┼───────────────────────────────────┼────────────────┤
  │ Accuracy           │ ≥ 80% values within 1% of ground  │ < 70%          │
  │                    │ truth                             │                │
  ├────────────────────┼───────────────────────────────────┼────────────────┤
  │ Latency            │ < 45s per page                    │ > 90s per page │
  ├────────────────────┼───────────────────────────────────┼────────────────┤
  │ Hallucination      │ < 10% of values fabricated        │ > 15%          │
  ├────────────────────┼───────────────────────────────────┼────────────────┤
  │ JSON validity      │ > 90% calls produce parseable     │ < 80%          │
  │                    │ JSON                              │                │
  ├────────────────────┼───────────────────────────────────┼────────────────┤
  │ Downstream         │ Output feeds numerical_module.py  │ Requires       │
  │ compatibility      │ without manual fix                │ manual repair  │
  └────────────────────┴───────────────────────────────────┴────────────────┘

  If 3b fails the Go criteria: upgrade to qwen2.5:7b (requires ~6GB VRAM on M1,
  borderline on 8GB unified memory). The 7b model is substantially better at
  instruction following and JSON.

  ---
  When you're ready to implement: set up Ollama, pull qwen2.5:3b, then I'll
  write the experiment script (extraction/llm_poc_experiment.py) as a
  self-contained file that touches nothing in the existing codebase.

✻ Cooked for 1m 2s

❯ /exit                                                                         
  ⎿  Goodbye!

❯ ohk ive installed ollam and run it using ollama run qwen2.5:3b , although     
  this model is not final , i am just looking if llm based extraction is bttr   
  .go on                                                                        

⏺ Let me check the canonical metrics and existing extraction structure before
  writing the script.

  Searched for 1 pattern (ctrl+o to expand)

⏺ Bash(ollama list 2>/dev/null)
  ⎿  NAME          ID              SIZE      MODIFIED
     qwen2.5:3b    357c53fb659c    1.9 GB    8 minutes ago

  Read 1 file (ctrl+o to expand)
⏺ Good, qwen2.5:3b is ready. Now writing the experiment script — isolated,
  touches nothing existing.

⏺ Bash(python3 -c "import ollama; print('ollama package available')" 2>/dev/null
      || echo "no ollama package"
      python3 -c "import fitz; print('pymupdf ok', fitz.__versio…)
  ⎿  no ollama package
     pymupdf ok 1.26.5

⏺ Will use requests to call Ollama's REST API directly. Writing the experiment
  now.

⏺ Write(extraction/llm_poc_experiment.py)
  ⎿  Wrote 399 lines to extraction/llm_poc_experiment.py
       2 llm_poc_experiment.py
       3 ---------------------
       4 Isolated proof-of-concept: LLM-based financial metric extraction
         .
       5 
       6 Pipeline:
       7   OFSS Annual Report PDF
       8     → PyMuPDF (text extraction)
       9     → page classifier (keyword-based, no ML)
      10     → Qwen2.5:3b via Ollama REST API
     … +447 lines (ctrl+o to expand)

⏺ Now let's run it from the project root:

⏺ Bash(python extraction/llm_poc_experiment.py 2>&1)
  ⎿  Error: Exit code 127
     (eval):1: command not found: python

⏺ Bash(python3 extraction/llm_poc_experiment.py 2>&1)
  ⎿  /Users/rishimane/Library/Python/3.9/lib/python/site-packages/urllib3/_
     _init__.py:35: NotOpenSSLWarning: urllib3 v2 only supports OpenSSL 1.1
     .1+, currently the 'ssl' module is compiled with 'LibreSSL 2.8.3'. See
     … +56 lines (ctrl+o to expand)
  ⎿  (timeout 5m)

⏺ The experiment ran. Let me read the full results to see what the model
  actually captured vs what it missed:

⏺ Bash(python3 -c "
      import json…)
  ⎿  === CALL STATS ===
     {'statement_type': 'balance_sheet', 'pages_used': [12, 15, 41], 'chars
     _sent': 4000, 'latency_s': 53.5, 'json_valid': True, 'metrics_returned
     … +38 lines (ctrl+o to expand)

⏺ Clear picture. Let me check what text was actually sent to the model so we
  understand the failure mode:

⏺ Bash(python3 -c "
      import fitz…)
  ⎿  === PAGE 9 ===
     Oracle Financial Services Software – Annual Report 2024-25
     Directors’ report
     … +47 lines (ctrl+o to expand)

⏺ That explains everything. Let me now do a quick scan to find where the actual
  financial statements are in this 193-page document:

⏺ Bash(python3 -c "
      import fitz…)
  ⎿  Page 56: total_assets + numbers (equity=False) — snippet: 53
     •
     Obtain sufficient appropriate audit evidence regarding the financial i
     … +2 lines (ctrl+o to expand)

⏺ Bash(python3 -c "
      import fitz…)
  ⎿  Page 9 [PL]: 37 numbers — Oracle Financial Services Software – Annual
     Report 2024-25
     Directors’ report
     … +52 lines (ctrl+o to expand)

⏺ Bash(python3 -c "
      import fitz, re…)
  ⎿  Page 61 [BS]: 22 numbers — Oracle Financial Services Software – Annual
      Report 2024-25
     Consolidated balance sheet
     … +14 lines (ctrl+o to expand)

⏺ The actual statements are on pages 61 (consolidated BS), 62 (consolidated
  P&L), 117-118 (consolidated CF), 129 (standalone BS), 130 (standalone P&L),
  177-178 (standalone CF). The classifier was sending page 12, 9, 36 — all
  wrong.

  The fix is simple: check only the first few lines of each page for statement
  titles, not the entire page. Let me patch the classifier:

⏺ Update(extraction/llm_poc_experiment.py)
  ⎿  Added 26 lines, removed 6 lines
       81  
       82  def find_statement_pages(pages: dict[int, str]) -> dict[str, l
           ist[int]]:
       83      """
       84 -    Returns {statement_type: [page_numbers]} for pages that co
          -ntain                                                         
       85 -    financial statement keywords.                             
       84 +    Returns {statement_type: [page_numbers]} for pages where a
          + financial                                                    
       85 +    statement TITLE appears in the first 8 lines AND the page 
          +has dense                                                     
       86 +    numeric content (>= 5 numbers of the form XX,XXX).        
       87 +                                                              
       88 +    Checks the page title area only — prevents false positives
          + from pages                                                   
       89 +    that merely mention keywords in body text (auditor notes, 
          +MDA, etc.).                                                   
       90      """
       87 -    found: dict[str, list[int]] = {k: [] for k in STATEMENT_KE
          -YWORDS}                                                       
       91 +    num_re = re.compile(r"\d{1,3}(?:,\d{3})+")  # e.g. 68,468 
          +or 1,89,483                                                   
       92 +                                                              
       93 +    # Tighter title-level keywords — must appear near top of p
          +age                                                           
       94 +    TITLE_KEYWORDS: dict[str, list[str]] = {                  
       95 +        "balance_sheet":    ["balance sheet", "statement of fi
          +nancial position"],                                           
       96 +        "income_statement": ["statement of profit and loss", "
          +income statement",                                            
       97 +                             "profit and loss account", "state
          +ment of operations"],                                         
       98 +        "cash_flow":        ["cash flow statement", "statement
          + of cash flows",                                              
       99 +                             "cash flows from operating"],    
      100 +    }                                                         
      101 +                                                              
      102 +    found: dict[str, list[int]] = {k: [] for k in TITLE_KEYWOR
          +DS}                                                           
      103 +                                                              
      104      for page_no, text in pages.items():
       89 -        lower = text.lower()                                  
       90 -        for stmt_type, keywords in STATEMENT_KEYWORDS.items():
       91 -            if any(kw in lower for kw in keywords):           
      105 +        # Only look at first 8 lines for the statement title  
      106 +        first_lines = "\n".join(text.splitlines()[:8]).lower()
      107 +        numeric_count = len(num_re.findall(text))             
      108 +                                                              
      109 +        for stmt_type, keywords in TITLE_KEYWORDS.items():    
      110 +            if any(kw in first_lines for kw in keywords) and n
          +umeric_count >= 5:                                            
      111                  found[stmt_type].append(page_no)
      112 +                                                              
      113      for stmt, pgs in found.items():
      114          print(f"[Classify] {stmt}: pages {pgs}")
      115      return found

⏺ Also update get_page_block to include the page itself plus just one following
  page (not two) since OFSS statements are compact, and increase the char limit
  to get the full table:

⏺ Update(extraction/llm_poc_experiment.py)
  ⎿  Added 1 line, removed 1 line
      58  }
      59  
      60  # Maximum characters sent per LLM call (keeps within 3b context
           limit)
      61 -MAX_CHARS_PER_CALL = 4000                                      
      61 +MAX_CHARS_PER_CALL = 6000                                      
      62  
      63  # -------------------------------------------------------------
          --------------
      64  # Step 1 — Extract text from PDF

⏺ Bash(python3 extraction/llm_poc_experiment.py 2>&1)
  ⎿  Running in the background (↓ to manage)
  ⎿  (timeout 5m)

  Read 1 file (ctrl+o to expand)
⏺ Experiment is running with the fixed page classifier. I'll update you when it
  finishes — all 3 Ollama calls take ~35s each, so expect ~2 minutes total.

  While we wait, here's what the first run told us:

  ---
  Run 1 Analysis:

  ┌──────────────────┬──────────────────────────────────────────────────────┐
  │     Finding      │                        Detail                        │
  ├──────────────────┼──────────────────────────────────────────────────────┤
  │ JSON validity    │ 100% — all 3 calls returned parseable JSON           │
  ├──────────────────┼──────────────────────────────────────────────────────┤
  │ Page classifier  │ Was sending page 9 (Directors' Report highlights),   │
  │ bug              │ 12 (governance text), 36 (CEO/CFO certificate) —     │
  │                  │ none were actual statements                          │
  ├──────────────────┼──────────────────────────────────────────────────────┤
  │ What the model   │ Correctly extracted revenue + net profit from the    │
  │ did right        │ Directors' Report summary table it was given — high  │
  │                  │ confidence, correct year tagging                     │
  ├──────────────────┼──────────────────────────────────────────────────────┤
  │ What the model   │ Returned null for all metrics it couldn't find in    │
  │ did right (2)    │ text — did NOT hallucinate values                    │
  ├──────────────────┼──────────────────────────────────────────────────────┤
  │ Latency          │ 53s (BS), 34s (P&L), 18s (CF) — heavier pages take   │
  │                  │ longer                                               │
  └──────────────────┴──────────────────────────────────────────────────────┘

  The 3b model's honesty (null instead of invented numbers) is actually a strong
  signal for feasibility.

  Run 2 sends the real pages: 61 (consolidated BS), 62 (P&L), 117 (CF), 129
  (standalone BS), 130 (standalone P&L), 177 (standalone CF).

✻ Cooked for 11m 31s

⏺ Background command "Re-run experiment with improved page classifier" completed
(exit code 0)

  Read 1 file (ctrl+o to expand)

⏺ Results are in. Two new issues surfaced — let me diagnose and fix them:

⏺ Bash(python3 -c "
      import fitz…)
  ⎿  Oracle Financial Services Software – Annual Report 2024-25
     Consolidated balance sheet
     as at March 31, 2025
     … +227 lines (ctrl+o to expand)

⏺ Clear diagnosis. Three issues to fix:

  1. num_predict=1500 too low — the model truncated mid-JSON on dense pages
  2. total_liabilities = total_assets — Indian BS format uses "TOTAL" twice
  (once for assets side, once for equity+liabilities side); model confused them
  3. Need JSON truncation recovery — salvage partial results when truncation
  occurs

⏺ Update(extraction/llm_poc_experiment.py)
  ⎿  Added 1 line, removed 1 line
      208          "stream": False,
      209          "options": {
      210              "temperature": 0,
      211 -            "num_predict": 1500,                              
      211 +            "num_predict": 2500,                              
      212          },
      213      }
      214  

⏺ Update(extraction/llm_poc_experiment.py)
  ⎿  Added 6 lines, removed 1 line
      239          parsed = json.loads(clean)
      240          return parsed, latency, True
      241      except json.JSONDecodeError as e:
      242 -        print(f"  [WARN] JSON parse failed: {e}")             
      242 +        print(f"  [WARN] JSON parse failed: {e} — attempting p
          +artial recovery")                                             
      243 +        # Try to salvage complete metric objects from truncate
          +d JSON                                                        
      244 +        recovered = _recover_partial_json(clean)              
      245 +        if recovered:                                         
      246 +            print(f"  [WARN] Recovered {len(recovered.get('met
          +rics', []))} metrics from truncated output")                  
      247 +            return recovered, latency, False                  
      248          print(f"  [WARN] Raw content (first 300 chars): {raw_c
           ontent[:300]}")
      249          return None, latency, False
      250  

⏺ Now add the _recover_partial_json function and update the system prompt. Let
  me add both:

⏺ Update(extraction/llm_poc_experiment.py)
  ⎿  Added 36 lines
      190  
      191  
      192  # ------------------------------------------------------------
           ---------------
      193 +# JSON truncation recovery                                    
      194 +# ------------------------------------------------------------
          +---------------                                               
      195 +                                                              
      196 +def _recover_partial_json(text: str) -> dict | None:          
      197 +    """                                                       
      198 +    Attempt to salvage complete metric objects from a truncate
          +d JSON string.                                                
      199 +    Finds all complete {...} metric blocks and wraps them in a
          + valid response.                                              
      200 +    """                                                       
      201 +    # Find all complete JSON objects inside the metrics array 
      202 +    objects = []                                              
      203 +    depth = 0                                                 
      204 +    start = -1                                                
      205 +    for i, ch in enumerate(text):                             
      206 +        if ch == "{":                                         
      207 +            if depth == 0:                                    
      208 +                start = i                                     
      209 +            depth += 1                                        
      210 +        elif ch == "}":                                       
      211 +            depth -= 1                                        
      212 +            if depth == 0 and start != -1:                    
      213 +                candidate = text[start:i+1]                   
      214 +                try:                                          
      215 +                    obj = json.loads(candidate)               
      216 +                    # Only keep objects that look like metric 
          +entries                                                       
      217 +                    if "canonical_name" in obj:               
      218 +                        objects.append(obj)                   
      219 +                except json.JSONDecodeError:                  
      220 +                    pass                                      
      221 +                start = -1                                    
      222 +                                                              
      223 +    if objects:                                               
      224 +        return {"metrics": objects, "notes": "recovered from t
          +runcated output"}                                             
      225 +    return None                                               
      226 +                                                              
      227 +                                                              
      228 +# ------------------------------------------------------------
          +---------------                                               
      229  # Step 4 — Call Ollama
      230  # ------------------------------------------------------------
           ---------------
      231  

⏺ Now fix the system prompt to handle the Indian balance sheet total confusion:

⏺ Update(extraction/llm_poc_experiment.py)
  ⎿  Added 6 lines
      150  6. Map each extracted item to the closest canonical_name from 
           the list provided.
      151  7. If a metric appears for multiple years, extract all of them
            as separate entries.
      152  8. Output ONLY a valid JSON object. No explanation text outsid
           e the JSON.
      153 +9. IMPORTANT — Indian Balance Sheet format: The word "TOTAL" a
          +ppears TWICE.                                                 
      154 +   First TOTAL = Total Assets (sum of all assets). Second TOTA
          +L = Total Equity + Liabilities.                               
      155 +   These two totals are always equal (balance sheet identity).
          + Do NOT treat the second                                      
      156 +   TOTAL as "total_liabilities". Extract "total_assets" from t
          +he first TOTAL only.                                          
      157 +   For total_liabilities: add non-current liabilities subtotal
          + + current liabilities subtotal.                              
      158 +10. Keep evidence snippets short (under 100 characters).      
      159  
      160  Output schema:
      161  {

⏺ Bash(python3 extraction/llm_poc_experiment.py 2>&1)
  ⎿  Running in the background (↓ to manage)

⏺ Running in background (~3 min). While it runs, here's a summary of what each
  fix targets:

  ┌───────────────────────────┬─────────────────────────────────────────────┐
  │            Fix            │              Problem it solves              │
  ├───────────────────────────┼─────────────────────────────────────────────┤
  │ num_predict: 2500         │ Income statement + cash flow JSON was       │
  │                           │ cutting off mid-object                      │
  ├───────────────────────────┼─────────────────────────────────────────────┤
  │ _recover_partial_json()   │ If truncation still happens, salvages all   │
  │                           │ complete {...} blocks already written       │
  ├───────────────────────────┼─────────────────────────────────────────────┤
  │ Prompt rule 9 (Indian BS  │ Model was assigning total_assets value to   │
  │ format)                   │ total_liabilities because "TOTAL" appears   │
  │                           │ twice in Indian balance sheets              │
  ├───────────────────────────┼─────────────────────────────────────────────┤
  │ Prompt rule 10 (short     │ Reduces output token usage, leaving more    │
  │ evidence)                 │ room for actual metric objects              │
  └───────────────────────────┴─────────────────────────────────────────────┘

✻ Churned for 1m 58s