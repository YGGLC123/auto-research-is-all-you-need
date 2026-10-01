# Finance Top-Journal Question Adapter

Use this as a finance field adapter for papers aiming at a top-finance-journal quality bar, including work that combines finance with machine learning, networks, hypergraphs, systemic risk, market microstructure, corporate finance, or asset pricing. It is a scientific interrogation template, not an official JF, RFS, JFE, or other journal submission rubric. Verify the selected journal's current format, data/code, disclosure, ethics, and submission rules separately.

The adapter asks what is uniquely financial: economic behavior, institutions, incentives, information sets, identification, equilibrium or transmission mechanisms, point-in-time data, economic magnitude, and domain-native alternatives. It does not reward an AI method merely for being applied to financial data.

## Compose the finance interrogation

Always consider:

- `FIN-CORE`: economic question, agents, mechanism, and counterfactual;
- `FIN-DATA`: construct validity, point-in-time lineage, and sample formation;
- `FIN-MAGNITUDE`: economic magnitude and decision value;
- `FIN-POSITIONING`: finance-literature contribution and nearest neighbours.

Add only when triggered:

- causal or policy claims -> `FIN-ID`;
- empirical panels, events, or time series -> `FIN-ECON`;
- prediction or machine learning -> `FIN-ML`;
- returns, portfolios, or asset pricing -> `FIN-AP`;
- networks or hypergraphs -> `FIN-NET`;
- contagion, fire sales, or systemic risk -> `FIN-SYS`;
- structural or equilibrium theory -> `FIN-THEORY`;
- extensive robustness or alternative mechanisms -> `FIN-ROBUST`;
- computational results or released assets -> `FIN-REPRO`.

For each selected question, use the generic evidence anchor, status, severity, claim at risk, evidence needed, owner, and stop condition. `NOT_APPLICABLE` is valid when justified.

## FIN-CORE — Economic question and mechanism

- `FIN-CORE.1` What price, allocation, financing choice, portfolio decision, liquidity condition, default risk, information flow, welfare object, or regulatory decision changes if the paper is right?
- `FIN-CORE.2` Why is this a finance question rather than a generic prediction, clustering, representation, or optimization exercise on financial data?
- `FIN-CORE.3` Who are the relevant agents, what do they know, what constraints and incentives do they face, and when do they act?
- `FIN-CORE.4` What economic mechanism connects the proposed object or treatment to the outcome?
- `FIN-CORE.5` Which institutional detail makes the mechanism plausible or testable?
- `FIN-CORE.6` What is the relevant counterfactual: without the shock, disclosure, constraint, ownership structure, signal, policy, or method, what would happen?
- `FIN-CORE.7` Is the claim about description, measurement, forecasting, causal effect, structural parameter, equilibrium response, or welfare?
- `FIN-CORE.8` What observable implication distinguishes the preferred mechanism from common shocks, selection, homophily, simultaneity, reverse causality, or omitted exposures?
- `FIN-CORE.9` Does the model allow agents to respond strategically to the signal, score, disclosure, or regulation?
- `FIN-CORE.10` What general economic lesson survives after removing the ML, graph, or hypergraph terminology?

## FIN-DATA — Construct validity and point-in-time lineage

- `FIN-DATA.1` What is the exact estimand or predictand, unit, horizon, sampling frame, and information set?
- `FIN-DATA.2` How does each proxy map to the intended latent construct, and what measurement error or alternative meaning remains?
- `FIN-DATA.3` Is “fragility,” “crowding,” “fire sale,” “contagion,” “stress,” “liquidity,” “risk,” or “attention” defined so that the label does not already assume the mechanism?
- `FIN-DATA.4` Was every feature publicly or privately observable to the stated decision maker at the claimed time?
- `FIN-DATA.5` Are economic period, reporting-as-of date, filing/release/acceptance time, amendment or revision time, and first tradable/usable time separated?
- `FIN-DATA.6` Are revised macro series, restated filings, final index membership, final classifications, merger outcomes, delisting status, or future identifier mappings backfilled into history?
- `FIN-DATA.7` Does the sample retain delisted, bankrupt, acquired, inactive, and failed entities when their outcomes matter?
- `FIN-DATA.8` Are CUSIP, ticker, PERMNO, LEI, fund, manager, issuer, and security mappings historically valid and auditable?
- `FIN-DATA.9` How are corporate actions, multiple share classes, options, shorts, leverage, derivatives, confidential holdings, amendments, and missing positions handled?
- `FIN-DATA.10` Are database coverage, reporting thresholds, vendor corrections, and survivorship selection part of the measurement model?
- `FIN-DATA.11` Can the exact point-in-time dataset be reconstructed from raw inputs, retrieval timestamps, hashes, parser versions, and append-only transformations?
- `FIN-DATA.12` Which leakage placebo—early release, final-restated history, current universe, latest vintage, random split—quantifies how much a naive pipeline overstates results?

Treat future or revised information entering features, topology, sample membership, normalization, labels, or benchmarks as `FATAL` when the headline result depends on it.

## FIN-ID — Causal identification and policy claims

- `FIN-ID.1` What is the causal estimand, treatment, outcome, timing, assignment mechanism, and target population?
- `FIN-ID.2` What source of variation identifies the effect, and why is it plausibly exogenous?
- `FIN-ID.3` Which assumption—parallel trends, exclusion, monotonicity, unconfoundedness, discontinuity continuity, random timing, or structural restriction—does the claim require?
- `FIN-ID.4` What observable pre-trend, balance, manipulation, first-stage, placebo, or falsification evidence bears on that assumption?
- `FIN-ID.5` Are treatment timing, anticipation, staggered adoption, dynamic effects, and post-treatment controls handled correctly?
- `FIN-ID.6` Do spillovers, network interference, equilibrium responses, or market-wide shocks violate SUTVA or the proposed design?
- `FIN-ID.7` Could selection into disclosure, network position, ownership, treatment, or sample be driven by the outcome?
- `FIN-ID.8` Are instruments relevant and defensibly excluded from the outcome except through treatment?
- `FIN-ID.9` Does a policy counterfactual require an equilibrium response that the reduced-form estimate cannot supply?
- `FIN-ID.10` If identification is insufficient, should the paper become a forecasting, measurement, or descriptive paper rather than retain causal language?

Prediction, feature importance, attention, deletion, permutation, or learned adjacency does not establish causal responsibility.

## FIN-ECON — Econometric design and inference

- `FIN-ECON.1` Does inference respect serial correlation, cross-sectional dependence, common shocks, clustering, and the actual treatment/decision unit?
- `FIN-ECON.2` Do overlapping return or risk horizons induce dependence that naive standard errors ignore?
- `FIN-ECON.3` Are network measures, residuals, fitted probabilities, factors, or exposures generated regressors whose estimation uncertainty must propagate?
- `FIN-ECON.4` Is the number of clusters sufficient, and are two-way, multi-way, spatial, or network dependence structures relevant?
- `FIN-ECON.5` Are fixed effects absorbing the variation needed to identify the mechanism, or failing to absorb obvious confounds?
- `FIN-ECON.6` How many outcomes, horizons, subgroups, signals, thresholds, networks, models, and specifications were searched?
- `FIN-ECON.7` Is there a locked primary test, holdout, family-wise/FDR adjustment, or honest post-selection interpretation?
- `FIN-ECON.8` Are effect sizes, confidence intervals, and economic magnitudes reported rather than p-values alone?
- `FIN-ECON.9` Do rare events, heavy tails, heteroskedasticity, missingness, censoring, or selection require different estimators or uncertainty procedures?
- `FIN-ECON.10` Are small-sample, weak-identification, weak-instrument, or near-singularity problems diagnosed?
- `FIN-ECON.11` Does the specification distinguish within-entity, between-entity, and time-series variation?
- `FIN-ECON.12` Can the core table be recreated under an alternative defensible inference design?

Ignoring overlapping horizons, dependence, generated regressors, estimated-network uncertainty, or broad specification search is a blocker and can be fatal when significance drives the contribution.

## FIN-ML — Machine learning and forecasting

- `FIN-ML.1` Is the task prediction, probability estimation, ranking, policy choice, causal discovery, or structural interpretation?
- `FIN-ML.2` Are prediction, explanation, and economic mechanism kept separate?
- `FIN-ML.3` Are train, validation, calibration, and test periods strictly ordered, with nested temporal tuning and all preprocessing fitted inside each fold?
- `FIN-ML.4` Is the final test sequence locked, or was it repeatedly used to choose targets, features, windows, losses, and architectures?
- `FIN-ML.5` Are label horizons purged or embargoed where observations overlap?
- `FIN-ML.6` Are baselines economically serious—domain indices, factor models, simple linear/logistic models, trees, pairwise networks—as well as algorithmically current?
- `FIN-ML.7` Does the method add information beyond prices, fundamentals, macro conditions, known holdings/crowding measures, and the best simple model?
- `FIN-ML.8` Are probability forecasts calibrated and evaluated with proper scores in addition to ranking metrics?
- `FIN-ML.9` For rare events, are precision-recall, operating-capacity metrics, false alarms, and warning lead time reported?
- `FIN-ML.10` Does performance survive realistic retraining frequency, data latency, crisis/calm regimes, market changes, and uncertainty across seeds/windows?
- `FIN-ML.11` Are model selection and repeated experimentation reflected in uncertainty and claim strength?
- `FIN-ML.12` What economic decision improves, at what cost, capacity, turnover, and risk, compared with a domain-native policy?
- `FIN-ML.13` Are attention or importance weights described only as fitted-model dependence unless independently validated?

Random cross-validation, test reuse, full-sample topology, or future-aware preprocessing is fatal to a temporal forecasting claim.

## FIN-AP — Asset-pricing and portfolio claims

- `FIN-AP.1` Is the return pattern compensation for risk, mispricing, limits to arbitrage, institutional demand, or construction artifact?
- `FIN-AP.2` What state variable or marginal utility exposure would support a risk-premium interpretation?
- `FIN-AP.3` Is alpha incremental to strong factor, characteristic, and domain-native benchmarks rather than only a weak model?
- `FIN-AP.4` Is performance concentrated in microcaps, illiquid names, stale prices, extreme observations, one era, or the short leg?
- `FIN-AP.5` Are breakpoints, weighting, rebalancing, execution delay, delisting returns, and missing returns economically defensible?
- `FIN-AP.6` Does the strategy survive bid-ask spreads, turnover, borrowing fees, shorting constraints, price impact, and capacity?
- `FIN-AP.7` Are look-ahead, publication lag, data revisions, index reconstitution, and universe construction point-in-time safe?
- `FIN-AP.8` Are portfolio sorts, cross-sectional regressions, and machine-learning tests answering the same claim or different ones?
- `FIN-AP.9` Does the effect survive alternative horizons, value/equal weighting, characteristic controls, and economically motivated subsamples?
- `FIN-AP.10` How much discovery search preceded the chosen signal, and what protects against a factor-zoo result?
- `FIN-AP.11` Is utility, certainty-equivalent, Sharpe improvement, drawdown, expected shortfall, or another economic objective reported with uncertainty?
- `FIN-AP.12` Does a profitable backtest imply an implementable trade for the relevant investor after information and trading frictions?

## FIN-NET — Network and hypergraph claims

- `FIN-NET.1` What does each node, edge, hyperedge, weight, direction, layer, and time index mean economically?
- `FIN-NET.2` Is the relation contractual exposure, common ownership, correlated holdings, information flow, collateral linkage, supply-chain connection, trading interaction, or only comovement?
- `FIN-NET.3` Why is a pairwise graph insufficient? What observable, theorem, or counterfactual exists only at higher order?
- `FIN-NET.4` Does a pairwise-preserving null destroy only higher-order organization while retaining node statistics and ordinary graph structure?
- `FIN-NET.5` Do strong pairwise graph, set, tensor, factor, and overlap/crowding baselines receive comparable capacity and information?
- `FIN-NET.6` Is topology estimated with future outcomes, complete-sample statistics, or downstream variables?
- `FIN-NET.7` Are network formation, portfolio choice, balance-sheet composition, and centrality endogenous?
- `FIN-NET.8` Can the analysis distinguish contagion or transmission from common shocks, homophily, reflection, and simultaneity?
- `FIN-NET.9` How does uncertainty in nodes, links, hyperedges, thresholds, weights, mappings, and missing disclosures propagate to estimates and predictions?
- `FIN-NET.10` Are results stable to economically plausible universes, boundary rules, hyperedge definitions, sparsification, normalization, and scale?
- `FIN-NET.11` Which known common-ownership, portfolio-overlap, fragility, crowded-trade, or systemic-risk measure is the closest economic baseline?
- `FIN-NET.12` Is “dynamic” based on true information arrival and version evolution, or only quarter/date labels assigned retrospectively?
- `FIN-NET.13` What economic mechanism corresponds to message passing or hypergraph aggregation?
- `FIN-NET.14` If higher-order gains vanish under pairwise-preserving tests, what honest contribution remains?

Using a hypergraph is not itself a finance contribution. The paper must establish higher-order necessity, incremental information, a new economic measurement, a mechanism, a boundary, or a decision consequence.

## FIN-SYS — Systemic risk, contagion, and fire-sale claims

- `FIN-SYS.1` What shock originates where, and through which contractual, funding, ownership, collateral, behavioral, or price-impact channel does it propagate?
- `FIN-SYS.2` What conservation, feasibility, balance-sheet, leverage, liquidity, or equilibrium constraints govern propagation?
- `FIN-SYS.3` Does a score predict fragility or realized stress, or identify an actual forced sale, contagion event, or causal spillover?
- `FIN-SYS.4` What independent flow, redemption, leverage, constraint, liquidation, price-pressure, or reversal evidence supports “fire sale” language?
- `FIN-SYS.5` Can common exposures or aggregate shocks explain the same co-movement without transmission?
- `FIN-SYS.6` What counterfactual intervention—capital, liquidity, disclosure, position limit, margin, or monitoring—does the model evaluate?
- `FIN-SYS.7` Does the intervention alter behavior or network formation in a way the static model ignores?
- `FIN-SYS.8` What loss, cascade, liquidity failure, welfare cost, or intervention ranking makes the risk measure economically useful?
- `FIN-SYS.9` Is systemic importance separated from vulnerability, contribution, exposure, and simple size?
- `FIN-SYS.10` Are crisis-period results a general mechanism or one historical episode?

Do not call common holdings or crowded portfolios a realized fire sale without independent forced-selling evidence. Prefer fragility, downside stress, or liquidity deterioration when that is what the label measures.

## FIN-THEORY — Financial theory and structural interpretation

- `FIN-THEORY.1` What agents, objectives, constraints, beliefs, information, timing, and market-clearing conditions define the model?
- `FIN-THEORY.2` What equilibrium object exists, is it unique, and which selection matters for the empirical claim?
- `FIN-THEORY.3` Which comparative static carries the economic insight rather than only mathematical tractability?
- `FIN-THEORY.4` Are functional forms and distributional assumptions essential or convenient?
- `FIN-THEORY.5` Does the model generate testable implications distinct from existing mechanisms?
- `FIN-THEORY.6` Which parameters are identified or calibrated, from which moments, and with what uncertainty?
- `FIN-THEORY.7` Can the model match the institutional timing and information available in the data?
- `FIN-THEORY.8` Does the welfare or policy conclusion survive equilibrium feedback and strategic behavior?
- `FIN-THEORY.9` Which boundary, impossibility, or necessity result would make the theory contribution sharper?

## FIN-MAGNITUDE — Economic significance and decisions

- `FIN-MAG.1` What is the effect or forecast improvement in economically interpretable units?
- `FIN-MAG.2` How large is it relative to baseline risk, typical variation, implementation cost, market capacity, policy cost, or welfare benchmark?
- `FIN-MAG.3` Who gains, who loses, and is the distributional effect relevant?
- `FIN-MAG.4` Does statistical significance survive while economic value is negligible—or vice versa?
- `FIN-MAG.5` What threshold would cause a real investor, intermediary, firm, exchange, or regulator to act?
- `FIN-MAG.6` Does acting on the result change prices or behavior enough to erode it?
- `FIN-MAG.7` Are benefits reported alongside false alarms, turnover, capital use, liquidity demand, and tail losses?

## FIN-ROBUST — Named alternatives and falsification

- `FIN-ROB.1` What is the strongest non-mechanism explanation, stated before selecting robustness tests?
- `FIN-ROB.2` Which placebo outcome, pseudo-event, pre-period, negative control, or randomization test should be null?
- `FIN-ROB.3` Which alternative data construction or measure would preserve the mechanism but change incidental choices?
- `FIN-ROB.4` Does the result survive leave-one-period, leave-one-institution, leave-one-industry, and regime splits where justified?
- `FIN-ROB.5` Does it survive common-factor, macro, liquidity, volatility, size, and institutional controls that map to named alternatives?
- `FIN-ROB.6` Are robustness checks diagnostic, or a large specification garden with only favorable results shown?
- `FIN-ROB.7` What result would cause the mechanism to be rejected rather than relabeled?

## FIN-REPRO — Finance-specific reproducibility

- `FIN-REPRO.1` Can raw-to-table lineage be reconstructed for every headline result?
- `FIN-REPRO.2` Are vendor versions, retrieval dates, security/fund identifiers, mapping tables, corporate-action treatment, and database filters preserved?
- `FIN-REPRO.3` Can a licensed-data user replay the exact build, and can an unlicensed reader run a synthetic or public smoke test?
- `FIN-REPRO.4` Are intermediate point-in-time snapshots versioned instead of overwritten by later corrections?
- `FIN-REPRO.5` Are portfolio, network, factor, and label construction scripts deterministic and configuration-driven?
- `FIN-REPRO.6` Do logs reveal every model, threshold, horizon, and sample variant searched?
- `FIN-REPRO.7` Can every table and figure be regenerated without manual spreadsheet steps?

## FIN-POSITIONING — Finance contribution and editorial memory

- `FIN-POS.1` What are the closest papers by economic question, mechanism, identification, data, method, and result?
- `FIN-POS.2` Which finance literature owns the baseline fact or mechanism that the paper currently presents as new?
- `FIN-POS.3` What does the paper add: economic fact, mechanism, identification, theory, measure/data, forecast, counterfactual, or decision tool?
- `FIN-POS.4` Is the contribution a new economic insight or only higher predictive performance?
- `FIN-POS.5` Does the method enable a finance question that existing tools could not answer, or simply re-estimate a known relation?
- `FIN-POS.6` What result would interest a finance reader who does not care about the method family?
- `FIN-POS.7` What is the strongest fair critique from an economist, an econometrician, a domain institutional expert, and a finance-ML reviewer?
- `FIN-POS.8` Which one sentence could a supportive editor use to explain the paper's financial importance and evidence?
- `FIN-POS.9` What is the strongest finance-safe title-level claim, and which causal or practical language must be removed?

## Fatal red flags

Treat these as fatal when the center claim depends on them:

- future, revised, or final-state information enters features, sample membership, topology, normalization, labels, or benchmarks;
- random cross-validation or repeated test reuse supports a temporal prediction claim;
- observational predictability is narrated as causal mechanism without identification;
- contemporaneous correlation or learned adjacency is called contagion without a propagation channel;
- topology or hyperedges use full-sample outcomes they later “predict”;
- higher-order gains disappear against pairwise-preserving nulls and strong pairwise/set/tensor baselines;
- inference ignores decisive dependence, overlapping horizons, generated regressors, two-stage estimation, or network-estimation uncertainty;
- the discovery is selected from many signals, outcomes, thresholds, networks, or windows without a locked primary test or multiplicity treatment;
- an asset-pricing result is driven by microcaps, infeasible shorts, stale prices, survivorship, or vanishes under realistic costs and capacity;
- an arbitrary network boundary or threshold determines the conclusion without economic interpretation;
- the method improves a metric but changes no economic knowledge, mechanism, measurement, counterfactual, or decision;
- the nearest finance literature already owns the headline contribution, leaving only data transfer or renaming;
- the headline result cannot be reproduced from preserved point-in-time lineage, code, and run configuration.

## Finance decision-docket output

Add only the selected fields to the generic output:

| Field | Required content |
|---|---|
| Economic question | Agent, decision, outcome, and counterfactual |
| Contribution type | Fact, mechanism, identification, theory, measure/data, forecast, or tool |
| Construct | Exact target, proxy, and alternative interpretation |
| Information set | As-of, observed/released, revised, and usable/tradable timing |
| Identification or prediction contract | Estimand/predictand, assumptions, split/design, and falsifier |
| Economic mechanism | Channel and competing explanations |
| Economic magnitude | Effect in decision-relevant units, costs, and uncertainty |
| Nearest finance neighbours | What they own and what remains incremental |
| Fatal threats | Point-in-time, identification, inference, mechanism, or novelty failures |
| Decisive assets | Theorem, design, table, figure, test, or dataset that would change judgment |
| Safe claims | Descriptive, predictive, causal, structural, or policy language actually supported |
| Next gate | One owning skill, expected artifact, fallback, and stop condition |

For a finance paper targeting an AI conference, combine this adapter with the live venue adapter. Finance questions govern economic validity; the venue adapter governs audience, track fit, current submission mechanics, and review presentation. Neither is allowed to erase the other.
