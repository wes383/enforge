"""Article-type guidance shared by planning, research and writing prompts.

The type is chosen when a project is created. It does not hard-code a section
template: it tells the agents what the article must accomplish, which research
material matters most and what reviewers should be able to check.
"""

from __future__ import annotations

from dataclasses import dataclass

from dr_core.db.enums import ArticleType, ProjectLanguage


@dataclass(frozen=True, slots=True)
class ArticleTypeGuide:
    key: str
    label_zh: str
    label_en: str
    intent_zh: str
    intent_en: str
    outline_zh: tuple[str, ...]
    outline_en: tuple[str, ...]
    research_zh: tuple[str, ...]
    research_en: tuple[str, ...]
    review_zh: tuple[str, ...]
    review_en: tuple[str, ...]

    def as_prompt(self, language: ProjectLanguage) -> dict:
        zh = language is ProjectLanguage.ZH
        return {
            "id": self.key,
            "label": self.label_zh if zh else self.label_en,
            "intent": self.intent_zh if zh else self.intent_en,
            "outline_focus": list(self.outline_zh if zh else self.outline_en),
            "research_focus": list(self.research_zh if zh else self.research_en),
            "review_focus": list(self.review_zh if zh else self.review_en),
        }


_GUIDES: dict[ArticleType, ArticleTypeGuide] = {
    ArticleType.EXPLAINER: ArticleTypeGuide(
        key=ArticleType.EXPLAINER.value,
        label_zh="解释型文章",
        label_en="Explainer",
        intent_zh="解释这是什么、如何运作，以及读者为什么需要理解它。",
        intent_en="Explain what something is, how it works and why the reader should understand it.",
        outline_zh=(
            "先界定解释目标与读者可能的知识缺口。",
            "拆解核心概念、组成部分、基本原理及其相互关系。",
            "按由浅入深的顺序建立解释链，抽象机制配具体实例。",
            "结尾检查解释的边界，说明简化可能遮蔽了什么。",
        ),
        outline_en=(
            "Define the explanation target and the reader's likely knowledge gap.",
            "Decompose the core concepts, parts, principles and their relationships.",
            "Build a shallow-to-deep explanation chain and make abstract mechanisms concrete.",
            "Close by checking the limits of the explanation and what simplification may hide.",
        ),
        research_zh=(
            "优先查找原始文献、官方说明、专业综述和可信的基础资料。",
            "确认定义、术语差异、运作机制与关键因果关系。",
            "寻找能说明抽象原理的实例，以及常见误解的可靠纠正。",
        ),
        research_en=(
            "Prefer primary documents, official explanations, expert overviews and foundational material.",
            "Confirm definitions, terminology, mechanisms and key causal links.",
            "Find concrete examples of abstract mechanisms and reliable corrections of common misunderstandings.",
        ),
        review_zh=("定义是否准确", "概念是否清晰", "解释链是否完整", "简化是否造成误导"),
        review_en=("definitional accuracy", "conceptual clarity", "completeness of the explanation", "misleading simplification"),
    ),
    ArticleType.MECHANISM_ANALYSIS: ArticleTypeGuide(
        key=ArticleType.MECHANISM_ANALYSIS.value,
        label_zh="机制分析",
        label_en="Mechanism Analysis",
        intent_zh="解释某种现象为何发生、为何持续存在。",
        intent_en="Explain why a phenomenon occurs and why it persists.",
        outline_zh=(
            "界定待解释的结果、发生条件与适用范围。",
            "提出竞争性解释，而不是一开始锁定单一原因。",
            "构建利益、激励、约束、信息与路径依赖之间的因果链。",
            "用能够区分不同解释的证据检验替代解释，说明适用条件。",
        ),
        outline_en=(
            "Define the outcome to explain, its conditions and scope.",
            "Set out competing explanations instead of committing to one cause up front.",
            "Build a causal chain across incentives, constraints, information and path dependence.",
            "Use evidence that discriminates between explanations, then state the boundary conditions.",
        ),
        research_zh=(
            "寻找数据、历史案例、跨地区比较、自然实验和过程证据。",
            "同时收集支持与反对每个竞争性解释的材料。",
            "关注反例、遗漏变量、替代机制和因果识别条件。",
        ),
        research_en=(
            "Look for data, historical cases, cross-regional comparisons, natural experiments and process evidence.",
            "Collect material for and against each competing explanation.",
            "Look for counterexamples, omitted variables, alternative mechanisms and identification conditions.",
        ),
        review_zh=("因果推断是否成立", "是否遗漏变量", "是否把相关关系误认为因果", "替代解释是否被认真检验"),
        review_en=("causal inference", "omitted variables", "correlation treated as causation", "serious testing of alternatives"),
    ),
    ArticleType.INVESTIGATIVE_RESEARCH: ArticleTypeGuide(
        key=ArticleType.INVESTIGATIVE_RESEARCH.value,
        label_zh="调查与深度研究",
        label_en="Investigative Research",
        intent_zh="围绕一个复杂议题，厘清已有事实、证据、争议与仍然未知的部分。",
        intent_en="Establish what is known, contested and still unknown about a complex issue.",
        outline_zh=(
            "界定主问题、子议题、时间范围与研究边界。",
            "按主题组织发现，交代来源优先级和多立场检索策略。",
            "建立证据台账式的讨论：事实、原文、时间、矛盾与核验状态。",
            "单列争议与缺口，最后区分已确认事实、合理推断和不确定性。",
        ),
        outline_en=(
            "Define the main issue, sub-issues, time range and research boundary.",
            "Organize findings by theme and explain source priority and multi-position search.",
            "Discuss evidence as a ledger: facts, quotations, dates, contradictions and verification status.",
            "Give contradictions and gaps their own treatment, then separate established facts, reasonable inferences and uncertainty.",
        ),
        research_zh=(
            "并行搜集官方资料、原始文件、研究论文、数据、新闻和当事方材料。",
            "主动寻找不同立场，交叉核验重要事实与时间线。",
            "记录来源可靠性、相互矛盾和仍缺失的关键证据。",
        ),
        research_en=(
            "Collect official material, primary documents, research, data, reporting and stakeholder accounts in parallel.",
            "Seek opposing positions and cross-check important facts and timelines.",
            "Record source reliability, contradictions and key evidence that is still missing.",
        ),
        review_zh=("来源可靠性", "多源交叉验证", "证据覆盖率", "重要事实是否遗漏", "结论是否超出证据"),
        review_en=("source reliability", "cross-source verification", "evidence coverage", "omitted facts", "conclusions beyond the evidence"),
    ),
    ArticleType.CASE_STUDY: ArticleTypeGuide(
        key=ArticleType.CASE_STUDY.value,
        label_zh="案例研究",
        label_en="Case Study",
        intent_zh="还原一个具体人物、事件、企业或项目经历了什么，以及为什么出现这样的结果。",
        intent_en="Reconstruct what happened in a specific case and why it produced that outcome.",
        outline_zh=(
            "说明案例选择理由、代表性与特殊性。",
            "界定时间、组织范围、外部环境和关键参与者。",
            "按时间线重建事件、关键决策、行动、变化与结果。",
            "分析决策与外部因素如何共同作用，提炼可迁移条件并讨论替代原因。",
        ),
        outline_en=(
            "Explain why this case was selected and what it is representative or exceptional for.",
            "Define the period, organizational boundary, external context and key actors.",
            "Reconstruct the sequence of events, decisions, actions, changes and outcomes.",
            "Analyze how decisions and external factors combined, then state transferable conditions and alternative explanations.",
        ),
        research_zh=(
            "结合当事方材料、独立报道、公开数据和其他可靠来源。",
            "建立时间线和关键决策表，识别利益相关方。",
            "寻找反事实线索、幸存者偏差和事后归因风险。",
        ),
        research_en=(
            "Combine stakeholder material, independent reporting, public data and other reliable sources.",
            "Build a timeline and decision table, and identify stakeholders.",
            "Look for counterfactual clues, survivorship bias and hindsight attribution.",
        ),
        review_zh=("是否以偏概全", "是否忽略替代原因", "是否存在幸存者偏差", "是否事后归因"),
        review_en=("overgeneralization", "alternative causes", "survivorship bias", "hindsight attribution"),
    ),
    ArticleType.COMPARATIVE_ANALYSIS: ArticleTypeGuide(
        key=ArticleType.COMPARATIVE_ANALYSIS.value,
        label_zh="比较与评估",
        label_en="Comparative Analysis",
        intent_zh="在共同标准下比较不同对象，并解释差异及其适用条件。",
        intent_en="Compare cases under shared criteria and explain differences and applicability.",
        outline_zh=(
            "说明比较对象、范围与选择理由。",
            "先建立共同维度、定义、指标和权重，再逐项比较。",
            "使用口径一致、时间窗口可比的证据构建比较矩阵。",
            "解释差异来源，形成条件化结论，而不是强行选出唯一赢家。",
        ),
        outline_en=(
            "Explain the objects, scope and selection rationale.",
            "Establish shared dimensions, definitions, indicators and weights before comparing.",
            "Use evidence with harmonized definitions and time windows to build a comparison matrix.",
            "Explain the sources of difference and reach conditional conclusions instead of forcing a single winner.",
        ),
        research_zh=(
            "优先统一数据口径、时间窗口、统计方式和资料质量。",
            "收集每个对象的背景条件、目标差异与约束。",
            "对关键指标做敏感性检查，避免权重决定结论。",
        ),
        research_en=(
            "Harmonize data definitions, time windows, measurement and source quality.",
            "Collect each object's context, objectives and constraints.",
            "Stress-test key indicators so weights do not silently determine the conclusion.",
        ),
        review_zh=("比较是否公平", "指标是否合理", "数据是否可比", "权重变化是否影响结论"),
        review_en=("fair comparison", "indicator validity", "data comparability", "weight sensitivity"),
    ),
    ArticleType.ARGUMENT_CRITIQUE: ArticleTypeGuide(
        key=ArticleType.ARGUMENT_CRITIQUE.value,
        label_zh="观点与批判性评论",
        label_en="Argument & Critique",
        intent_zh="检验一个观点是否成立，并识别流行解释或既有观念中的问题。",
        intent_en="Test whether a claim holds and identify weaknesses in a popular explanation.",
        outline_zh=(
            "明确核心主张、关键概念与讨论范围。",
            "拆解主要论点、前提、依据及其逻辑关系。",
            "呈现支持证据，同时认真建立最强反方观点。",
            "安排主张、依据、反驳、回应与结论，并检查逻辑跳跃。",
        ),
        outline_en=(
            "State the core claim, key concepts and scope.",
            "Lay out the main arguments, premises, support and logical relations.",
            "Present supporting evidence and the strongest opposing case.",
            "Arrange claim, support, objection, response and conclusion, and check for logical gaps.",
        ),
        research_zh=(
            "寻找直接支持关键主张的事实、数据、案例和理论。",
            "主动寻找有分量的反方论证，而不是挑选容易驳倒的弱论点。",
            "区分事实判断与价值判断，检查概念混淆和选择性举例。",
        ),
        research_en=(
            "Find facts, data, cases and theory that directly support the core claim.",
            "Seek serious opposing arguments rather than weak straw men.",
            "Separate factual from normative judgments and check conceptual confusion and selective examples.",
        ),
        review_zh=("论证是否有效", "反方是否有代表性", "证据是否充分", "事实判断与价值判断是否混淆"),
        review_en=("argument validity", "representativeness of the opposition", "evidential sufficiency", "facts versus values"),
    ),
    ArticleType.TREND_SCENARIO: ArticleTypeGuide(
        key=ArticleType.TREND_SCENARIO.value,
        label_zh="趋势与情景分析",
        label_en="Trend & Scenario Analysis",
        intent_zh="解释一个领域正在发生什么变化，以及未来可能沿哪些路径发展。",
        intent_en="Explain what is changing in a field and which future paths are plausible.",
        outline_zh=(
            "界定趋势对象、指标、历史基准期和观察窗口。",
            "梳理起点、速度、阶段波动和领先指标，识别驱动因素。",
            "区分已有较强证据的变化与仍不确定的条件。",
            "构建竞争性情景，说明成立条件、机制、影响和需要观察的信号。",
        ),
        outline_en=(
            "Define the trend, indicators, historical baseline and observation window.",
            "Trace origins, pace, phases, leading indicators and drivers.",
            "Separate changes with strong evidence from conditions that remain uncertain.",
            "Build competing scenarios with conditions, mechanisms, implications and signals to watch.",
        ),
        research_zh=(
            "搜集时间序列、领先指标、政策与技术变化、经济和社会行为数据。",
            "寻找基准率和历史类比，避免把过去趋势直线外推。",
            "明确关键不确定性及其对结论的影响。",
        ),
        research_en=(
            "Collect time series, leading indicators, policy and technology changes, and economic or social behavior data.",
            "Use base rates and historical analogies instead of extrapolating past trends in a straight line.",
            "Identify key uncertainties and their effect on the conclusions.",
        ),
        review_zh=("数据是否可靠", "假设是否透明", "是否考虑替代情景", "是否夸大预测精度"),
        review_en=("data reliability", "transparent assumptions", "alternative scenarios", "false precision"),
    ),
    ArticleType.DECISION_STRATEGY: ArticleTypeGuide(
        key=ArticleType.DECISION_STRATEGY.value,
        label_zh="决策与策略报告",
        label_en="Decision & Strategy",
        intent_zh="面对具体问题，在约束与不确定性下给出可执行的行动建议。",
        intent_en="Recommend action under explicit constraints and uncertainty.",
        outline_zh=(
            "定义决策目标、约束、成功标准和不可妥协的要求。",
            "列出备选方案，包括维持现状。",
            "统一评估成本、收益、风险、可行性与长期影响。",
            "分析权衡与敏感性，给出推荐方案、实施步骤、验证指标、风险应对和备选路径。",
        ),
        outline_en=(
            "Define the decision, constraints, success criteria and non-negotiables.",
            "List options, including keeping the status quo.",
            "Assess costs, benefits, risks, feasibility and long-term effects on shared criteria.",
            "Analyze tradeoffs and sensitivity, then give a recommendation, implementation steps, validation metrics, risk responses and fallbacks.",
        ),
        research_zh=(
            "搜集成本、收益、风险、资源需求、依赖关系和可行性证据。",
            "研究类似决策的实际结果，而不只采用方案提供方的材料。",
            "检查不同预算、假设和风险偏好下推荐是否改变。",
        ),
        research_en=(
            "Collect evidence on costs, benefits, risks, resource needs, dependencies and feasibility.",
            "Study actual outcomes of similar decisions, not only material from option sponsors.",
            "Test whether the recommendation changes under different budgets, assumptions or risk preferences.",
        ),
        review_zh=("建议是否有证据支撑", "成本与风险是否完整", "执行条件是否现实", "结论对假设是否敏感"),
        review_en=("evidence-backed recommendation", "complete costs and risks", "execution realism", "sensitivity to assumptions"),
    ),
    ArticleType.HISTORICAL_DEVELOPMENT: ArticleTypeGuide(
        key=ArticleType.HISTORICAL_DEVELOPMENT.value,
        label_zh="历史演进与发展史",
        label_en="Historical Development",
        intent_zh="解释一个事物如何一步步发展成今天的样子。",
        intent_en="Explain how something developed step by step into its present form.",
        outline_zh=(
            "界定历史对象、起止时间、地域和讨论边界。",
            "追溯起源与前身，区分早期思想、初步实践、正式发明和普及。",
            "建立时间线，按有依据的标准划分阶段。",
            "解释转折点与演变机制，比较不同历史解释，说明连续性与断裂。",
        ),
        outline_en=(
            "Define the historical object, period, geography and boundary.",
            "Trace origins and precursors, distinguishing ideas, early practice, invention and diffusion.",
            "Build a timeline and divide it into phases using explicit criteria.",
            "Explain turning points and mechanisms, compare interpretations, and distinguish continuity from rupture.",
        ),
        research_zh=(
            "优先查找原始文献、档案、当时出版物、可靠历史研究和相关数据。",
            "核对年代、人物、事件先后关系与不同资料的冲突。",
            "避免从今天的成功形态倒推历史必然性，保留当时存在的其他可能路径。",
        ),
        research_en=(
            "Prefer primary documents, archives, period publications, reliable historical scholarship and data.",
            "Verify dates, actors, sequence and conflicts between sources.",
            "Avoid reading history backwards from today's outcome; recover alternatives that existed at the time.",
        ),
        review_zh=("事实与年代准确性", "史料可靠性", "分期依据", "转折点解释", "是否倒推历史必然性"),
        review_en=("factual and chronological accuracy", "source reliability", "periodization", "turning-point explanation", "teleology"),
    ),
}

def article_type_guide(article_type: ArticleType) -> ArticleTypeGuide:
    return _GUIDES[article_type]


def article_type_prompt(article_type: ArticleType, language: ProjectLanguage) -> dict:
    return article_type_guide(article_type).as_prompt(language)
