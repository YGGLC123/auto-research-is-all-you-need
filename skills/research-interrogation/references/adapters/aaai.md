# AAAI Venue Adapter

This is a thin venue lens for the universal question suite. Load it only when the exact target is an AAAI conference track or the user explicitly asks for an AAAI-style stress test. It does not define the scientific problem, replace field-specific standards, or force modern-AI/LLM framing.

## Live venue contract

Before applying venue-specific questions, verify official current-cycle sources for:

- conference year and exact track;
- submission, supplement, code/data, notification, and response dates;
- main-content and total-page limits;
- anonymity and paper-modification rules;
- review phases and whether an early-rejected paper can respond;
- track-specific evaluation criteria;
- reproducibility, ethics, generative-AI, overlapping-work, and multiple-submission policies.

Record source URLs and verification date. Mark anything unverified `VERIFY_REQUIRED`. Never inherit a previous year's page count, review process, reviewer roles, or special-track rubric.

## AAAI-specific questions

- `AAAI.1` Which exact track is the paper targeting, and why is that track's current rubric the right one?
- `AAAI.2` Is the contribution theoretical, methodological, algorithmic, empirical, integrative, critical, or a defensible combination?
- `AAAI.3` What makes the problem and result relevant to a broad AI audience rather than only a narrow application community?
- `AAAI.4` If this is a domain application, what is novel and justified in the AI approach, and what domain-native literature and baselines are engaged?
- `AAAI.5` If this is theory, can a broad AI reviewer understand the important capability or boundary before the full formalism?
- `AAAI.6` Does the paper offer interest beyond a small incremental benchmark gain or a narrow subarea?
- `AAAI.7` What would a fast first-stage reader understand from the title, abstract, first page, central figure/table, and contribution list under the current review process?
- `AAAI.8` Which fatal misunderstanding cannot rely on author response and must be prevented in the main paper?
- `AAAI.9` Is decision-critical material incorrectly delegated to supplementary material that reviewers may not be required to read?
- `AAAI.10` Does the paper's primary keyword/area choice route it to reviewers who can judge both the problem and method?
- `AAAI.11` Do claims, responsible-research practices, ethics, data/model use, and artifact documentation satisfy the current official policy?
- `AAAI.12` If overlapping-author or related submissions exist, is each contribution independently identifiable under the current policy?
- `AAAI.13` Does any modern-AI, LLM, agent, safety, alignment, or social-impact positioning follow from a faithful object-to-workflow mapping, or is it trend washing?
- `AAAI.14` If a special track is considered, does the work satisfy that track's actual substantive rubric rather than only its keywords?
- `AAAI.15` What is the most likely AAAI decision-level objection after field-specific validity and novelty questions are answered?

## Reviewer lenses

Select roles from the live review process, normally including a broad AI reader, the closest technical expert, the paper-type validity specialist, a framing sceptic only when relevant, and the decision synthesizer. Do not hard-code “Senior AC,” review counts, or phases that the current cycle does not use.

## Page and front-end pressure

Use the verified current page limit as a variable. Allocate main-paper space to the real problem, decisive definition/mechanism, strongest result, nearest-prior difference, critical evidence, and visible boundary. Every insertion names the deletion, compression, or move that pays for it. Do not turn an old seven-page storyboard into a universal law.

## Optional modern-AI mapping

Activate the generic L0–L4 questions only when such positioning is part of the intended paper. AAAI relevance does not require an LLM claim. A strong KRR, search, planning, reasoning, learning, multiagent, robotics, vision, NLP, data mining, human-centered, critical, or integrative contribution can stand on its own when it fits the current scope.

## Adapter output

Add only these fields to the generic decision docket:

- verified AAAI year/track contract;
- broad-AI relevance statement;
- current-track fit and routing risk;
- fast-review failure point;
- main-versus-supplement consequence;
- current-policy blocker;
- one AAAI-specific repair or safe repositioning.

All literature, theory, domain validity, experiments, writing, figures, and reproducibility work remains owned by the generic suite and phase skills.
