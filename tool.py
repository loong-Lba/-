#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import re
import sys
import textwrap
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

# 默认走 DashScope 的 OpenAI 兼容接口；mock 模式不会用到这些配置。
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-plus"
DEFAULT_STREAMING = True
DEFAULT_BATCH_SIZE = 8
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 内部用英文 code，报告里再转成业务方能看懂的中文名。
ISSUE_TYPE_LABELS = {
    "rule_conflict": "规则冲突类",
    "duplicate_conflict": "重复冲突类",
    "empty_or_incomplete_answer": "空白/未完成类",
    "incomplete_info": "信息不完整类",
    "stale_risk": "时效高风险类",
    "coverage_gap": "覆盖缺失类",
}
ALLOWED_ISSUE_TYPES = set(ISSUE_TYPE_LABELS)
ALLOWED_SEVERITIES = {"P0", "P1", "P2", "P3"}

# 兼容 LLM 偶尔返回中文分类名的情况，方便后面统一处理。
ISSUE_TYPE_ALIASES = {
    "规则冲突类": "rule_conflict",
    "规则冲突": "rule_conflict",
    "重复冲突类": "duplicate_conflict",
    "重复冲突": "duplicate_conflict",
    "空白/未完成类": "empty_or_incomplete_answer",
    "空白未完成类": "empty_or_incomplete_answer",
    "空白类": "empty_or_incomplete_answer",
    "未完成类": "empty_or_incomplete_answer",
    "empty_answer": "empty_or_incomplete_answer",
    "信息不完整类": "incomplete_info",
    "信息不完整": "incomplete_info",
    "时效高风险类": "stale_risk",
    "时效风险类": "stale_risk",
    "过时风险类": "stale_risk",
    "stale": "stale_risk",
    "覆盖缺失类": "coverage_gap",
    "覆盖缺失": "coverage_gap",
}

# mock 结果主要用来演示完整流程：没配 API Key 时也能跑出报告。
MOCK_ARTICLE_ISSUES = {
    "KB001": [
        {
            "issue_type": "duplicate_conflict",
            "severity": "P0",
            "summary": "与 KB039 回答同一问题但口径冲突",
            "detail": "两条 FAQ 都在回答退货政策，但一条写 7 天无理由、一条写 30 天无理由，会导致搜索命中结果不稳定。",
            "impact": "用户可能得到相互矛盾的退货政策答案，影响平台可信度。",
            "evidence": "KB001 问题与 KB039 问题相同：退货政策是什么？",
            "suggestion": "保留一条标准答案，合并或下线另一条，并建立 canonical FAQ。",
            "confidence": 0.97,
        }
    ],
    "KB002": [
        {
            "issue_type": "rule_conflict",
            "severity": "P0",
            "summary": "退货运费承担规则与基准文档冲突",
            "detail": "条目写成所有退货运费都由商家承担，但基准文档区分了质量问题与非质量问题。",
            "impact": "会直接误导用户预期并增加售后争议。",
            "evidence": "FAQ: 所有退货的运费都由商家承担；规则: 非质量问题买家承担、质量问题商家承担。",
            "suggestion": "改写为按质量问题与非质量问题分别说明运费承担方。",
            "confidence": 0.99,
        }
    ],
    "KB005": [
        {
            "issue_type": "rule_conflict",
            "severity": "P0",
            "summary": "发货时效与物流口径与基准文档冲突",
            "detail": "条目写成 48 小时内发货、使用顺丰、2-3 天到货，与 24 小时内发货、中通/韵达/圆通、3-5 天到货冲突。",
            "impact": "会让用户对履约承诺形成错误预期。",
            "evidence": "FAQ: 48小时内发货，顺丰快递，2-3天到货；规则: 24小时内发货，中通/韵达/圆通，3-5天到货。",
            "suggestion": "按基准文档重写物流标准口径，并与发货时效 FAQ 合并治理。",
            "confidence": 0.99,
        },
        {
            "issue_type": "duplicate_conflict",
            "severity": "P0",
            "summary": "与 KB020 在发货时效主题上口径不一致",
            "detail": "KB005 与 KB020 都在回答发货时效，但前者为 48 小时，后者为 24 小时内。",
            "impact": "同主题多个 FAQ 不一致会降低知识库命中稳定性。",
            "evidence": "KB005: 48小时内发货；KB020: 一般24小时内发货。",
            "suggestion": "统一发货时效 FAQ，仅保留一条标准答案。",
            "confidence": 0.95,
        },
        {
            "issue_type": "stale_risk",
            "severity": "P3",
            "summary": "物流承诺类条目更新时间较早",
            "detail": "物流属于高变动信息，且该条目长期未更新，存在口径继续过时的风险。",
            "impact": "即使当前修正后正确，也需要纳入周期性复核。",
            "evidence": "updated_at = 2023-05-20。",
            "suggestion": "加入高变动条目复核清单。",
            "confidence": 0.88,
        },
    ],
    "KB006": [
        {
            "issue_type": "rule_conflict",
            "severity": "P0",
            "summary": "快递公司说明与基准文档冲突",
            "detail": "条目把顺丰写成主要承运方，但基准文档明确合作快递为中通、韵达、圆通且系统自动分配。",
            "impact": "会误导用户对承运范围和指定能力的理解。",
            "evidence": "FAQ: 使用顺丰快递发货；规则: 中通、韵达、圆通（系统自动分配）。",
            "suggestion": "改写为合作快递清单与自动分配逻辑。",
            "confidence": 0.98,
        },
        {
            "issue_type": "stale_risk",
            "severity": "P3",
            "summary": "物流类条目更新时间较早",
            "detail": "物流类信息更新频繁，旧条目即使修改后也应纳入定期复核。",
            "impact": "长期不复核容易再次沉积错误口径。",
            "evidence": "updated_at = 2023-05-20。",
            "suggestion": "加入季度复核机制。",
            "confidence": 0.84,
        },
    ],
    "KB008": [
        {
            "issue_type": "rule_conflict",
            "severity": "P0",
            "summary": "把货到付款写成支持，与基准文档冲突",
            "detail": "基准文档明确不支持货到付款，但条目给出了支持性的明确回答。",
            "impact": "直接影响支付预期，容易造成下单失败与投诉。",
            "evidence": "FAQ: 支持货到付款；规则: 不支持货到付款。",
            "suggestion": "改成不支持货到付款，并列出支持的支付方式。",
            "confidence": 0.99,
        }
    ],
    "KB010": [
        {
            "issue_type": "rule_conflict",
            "severity": "P0",
            "summary": "发票类型与申请方式均与基准文档冲突",
            "detail": "条目写成支持纸质发票，且让用户在下单备注填写信息，与“仅支持电子发票、订单详情页申请”冲突。",
            "impact": "会让用户按照错误路径申请发票。",
            "evidence": "FAQ: 支持电子发票和纸质发票；规则: 仅支持电子发票（订单详情页申请）。",
            "suggestion": "重写为标准发票 FAQ，明确发票类型与申请入口。",
            "confidence": 0.99,
        }
    ],
    "KB011": [
        {
            "issue_type": "rule_conflict",
            "severity": "P0",
            "summary": "发票申请流程与基准文档冲突",
            "detail": "条目写成发货时寄出纸质发票，与当前电子发票在线申请规则不符。",
            "impact": "错误流程会导致用户重复咨询客服。",
            "evidence": "FAQ: 发货时一起寄出纸质发票；规则: 订单详情页申请电子发票。",
            "suggestion": "下线旧说法，统一到一条标准发票申请 FAQ。",
            "confidence": 0.99,
        }
    ],
    "KB012": [
        {
            "issue_type": "rule_conflict",
            "severity": "P0",
            "summary": "会员门槛与折扣规则都与基准文档冲突",
            "detail": "银卡和金卡的升级门槛、折扣力度均与当前业务规则不一致。",
            "impact": "会直接影响用户对权益的理解和投诉风险。",
            "evidence": "FAQ: 银卡满1000元9折、金卡满5000元85折；规则: 银卡满2000元95折、金卡满8000元9折。",
            "suggestion": "重写会员权益 FAQ，并将会员类条目纳入周期复核。",
            "confidence": 0.99,
        },
        {
            "issue_type": "stale_risk",
            "severity": "P3",
            "summary": "会员权益类条目具有高时效风险",
            "detail": "会员门槛和折扣是高变动规则，旧版本容易残留。",
            "impact": "后续即使修正，也需要持续复核。",
            "evidence": "updated_at = 2023-07-15。",
            "suggestion": "建立会员权益版本更新同步机制。",
            "confidence": 0.87,
        },
    ],
    "KB014": [
        {
            "issue_type": "rule_conflict",
            "severity": "P0",
            "summary": "优惠券活动面额与门槛与基准文档冲突",
            "detail": "条目写成满300减50和满600减120，而规则是满200减20、满500减60。",
            "impact": "直接影响用户优惠预期与转化体验。",
            "evidence": "FAQ: 满300减50、满600减120；规则: 满200减20、满500减60。",
            "suggestion": "按当前活动重写优惠券 FAQ。",
            "confidence": 0.99,
        },
        {
            "issue_type": "stale_risk",
            "severity": "P3",
            "summary": "优惠活动类条目时效性极高",
            "detail": "活动类信息天然高变动，需要独立复核机制。",
            "impact": "旧活动信息会快速失真。",
            "evidence": "优惠活动属于高时效业务信息。",
            "suggestion": "增加活动有效期与下线流程。",
            "confidence": 0.89,
        },
    ],
    "KB015": [
        {
            "issue_type": "rule_conflict",
            "severity": "P0",
            "summary": "把优惠券写成可叠加，与基准文档冲突",
            "detail": "规则明确不叠加使用，但条目写成最多叠加 3 张。",
            "impact": "会误导用户结算预期并触发投诉。",
            "evidence": "FAQ: 最多叠加3张；规则: 优惠券不叠加使用。",
            "suggestion": "修正为不可叠加，并在结算帮助中补充说明。",
            "confidence": 0.99,
        },
        {
            "issue_type": "stale_risk",
            "severity": "P3",
            "summary": "优惠规则类条目存在时效风险",
            "detail": "优惠规则容易变更，需要持续复核。",
            "impact": "过时信息会持续影响转化与客服咨询量。",
            "evidence": "updated_at = 2023-11-01。",
            "suggestion": "纳入营销规则版本同步机制。",
            "confidence": 0.85,
        },
    ],
    "KB016": [
        {
            "issue_type": "rule_conflict",
            "severity": "P0",
            "summary": "在线客服时间与基准文档冲突",
            "detail": "条目写成 7x24，但规则是 9:00-22:00。",
            "impact": "会让用户在非服务时段形成错误预期。",
            "evidence": "FAQ: 7x24小时全天候服务；规则: 在线客服 9:00-22:00。",
            "suggestion": "统一客服 FAQ 口径，并补充人工转接说明。",
            "confidence": 0.99,
        },
        {
            "issue_type": "stale_risk",
            "severity": "P3",
            "summary": "客服时间类条目需持续复核",
            "detail": "客服时间属于高变动信息，旧条目容易滞留。",
            "impact": "时段错误会直接影响用户体验。",
            "evidence": "updated_at = 2023-03-01。",
            "suggestion": "建立客服渠道定期复核机制。",
            "confidence": 0.88,
        },
    ],
    "KB017": [
        {
            "issue_type": "incomplete_info",
            "severity": "P2",
            "summary": "电话客服时间补充了工作日限定，需要业务确认",
            "detail": "基准文档只给出 9:00-18:00，没有明确仅限工作日，当前 FAQ 可能扩展了口径。",
            "impact": "会造成渠道时段理解偏差。",
            "evidence": "FAQ: 工作日9:00-18:00；规则: 电话客服 9:00-18:00。",
            "suggestion": "与业务确认是否仅限工作日，确认后补全标准说法。",
            "confidence": 0.73,
        },
        {
            "issue_type": "stale_risk",
            "severity": "P3",
            "summary": "客服时间类条目需周期复核",
            "detail": "客服时段属于高变动信息。",
            "impact": "旧时段信息会影响用户联系预期。",
            "evidence": "updated_at = 2023-03-01。",
            "suggestion": "加入客服类条目复核清单。",
            "confidence": 0.82,
        },
    ],
    "KB020": [
        {
            "issue_type": "incomplete_info",
            "severity": "P2",
            "summary": "补充了大促期间 48 小时例外，但基准文档未覆盖",
            "detail": "当前条目增加了业务例外说明，不能直接判错，但需要确认是否仍为有效口径。",
            "impact": "若例外规则已失效，会导致时效承诺偏差。",
            "evidence": "FAQ: 大促期间可能延长至48小时；规则: 下单后24小时内发货（预售除外）。",
            "suggestion": "与业务确认大促例外是否有效，必要时把例外写入正式规则文档。",
            "confidence": 0.77,
        },
        {
            "issue_type": "stale_risk",
            "severity": "P3",
            "summary": "物流时效例外规则存在时效风险",
            "detail": "大促例外规则容易过期，需要定期复核。",
            "impact": "陈旧例外会让标准时效说明失真。",
            "evidence": "updated_at = 2024-02-01。",
            "suggestion": "为营销/大促类例外增加时效标记。",
            "confidence": 0.8,
        },
    ],
    "KB024": [
        {
            "issue_type": "incomplete_info",
            "severity": "P2",
            "summary": "换货说明缺少适用条件和费用边界",
            "detail": "当前说法更像“退货后重下单”，没有说明是否存在标准换货流程、适用商品和运费规则。",
            "impact": "用户看完仍无法准确执行换货操作。",
            "evidence": "FAQ 只写“先退货再重新下单选择正确规格”。",
            "suggestion": "补充换货适用场景、标准流程、费用承担与不支持场景。",
            "confidence": 0.92,
        }
    ],
    "KB030": [
        {
            "issue_type": "incomplete_info",
            "severity": "P2",
            "summary": "人工客服说明缺少服务时间与其他渠道信息",
            "detail": "当前只告诉用户输入“转人工”，但没有补充人工服务时间，也没有连到电话/邮件客服。",
            "impact": "会增加二次咨询和转人工失败后的困惑。",
            "evidence": "FAQ 只写“输入转人工即可转接人工客服”。",
            "suggestion": "补充人工服务时段，并串联电话/邮件客服渠道。",
            "confidence": 0.93,
        }
    ],
    "KB032": [
        {
            "issue_type": "empty_or_incomplete_answer",
            "severity": "P1",
            "summary": "条目答案为空",
            "detail": "问题已建档但没有任何答案内容，用户命中后无法自助解决。",
            "impact": "知识库存在“看得到问题、拿不到答案”的明显缺口。",
            "evidence": "answer 为空字符串。",
            "suggestion": "优先补齐售后保修 FAQ，如暂未支持应明确说明。",
            "confidence": 1.0,
        }
    ],
    "KB033": [
        {
            "issue_type": "incomplete_info",
            "severity": "P2",
            "summary": "分期细节超出基准文档明确范围，需要核实",
            "detail": "基准文档只明确支持花呗，未明确 3/6/12 期和手续费口径。",
            "impact": "若条目扩展了未经确认的能力，会误导支付预期。",
            "evidence": "FAQ 说明 3/6/12 期分期与手续费；规则只写支持花呗。",
            "suggestion": "与支付业务确认分期能力后，再补充正式 FAQ。",
            "confidence": 0.79,
        },
        {
            "issue_type": "stale_risk",
            "severity": "P3",
            "summary": "支付能力细节属于高时效信息",
            "detail": "支付分期和手续费规则容易调整。",
            "impact": "过期支付规则会直接影响下单体验。",
            "evidence": "updated_at = 2023-08-15。",
            "suggestion": "支付规则变更后同步更新知识库。",
            "confidence": 0.81,
        },
    ],
    "KB037": [
        {
            "issue_type": "empty_or_incomplete_answer",
            "severity": "P1",
            "summary": "条目答案为空",
            "detail": "账号注销是高敏感用户诉求，但当前条目没有可执行答案。",
            "impact": "用户无法自助完成账号注销相关操作，会直接转人工。",
            "evidence": "answer 为空字符串。",
            "suggestion": "优先补齐账号注销入口、条件和影响说明。",
            "confidence": 1.0,
        }
    ],
    "KB039": [
        {
            "issue_type": "rule_conflict",
            "severity": "P0",
            "summary": "退货政策核心口径与基准文档冲突",
            "detail": "条目写成 30 天无理由退货且所有退货运费商家承担，与当前规则均不一致。",
            "impact": "会对退货承诺产生严重误导。",
            "evidence": "FAQ: 30天无理由退货，所有退货运费商家承担；规则: 7天无理由、按问题类型区分运费承担。",
            "suggestion": "下线或重写该条目，并与 KB001 合并治理。",
            "confidence": 1.0,
        },
        {
            "issue_type": "duplicate_conflict",
            "severity": "P0",
            "summary": "与 KB001 回答同一问题但答案冲突",
            "detail": "两条 FAQ 的问题相同，但政策口径明显不一致。",
            "impact": "搜索命中结果不可控，严重损害知识库可信度。",
            "evidence": "KB001 与 KB039 的 question 完全相同。",
            "suggestion": "保留一条标准退货政策 FAQ，删除或合并另一条。",
            "confidence": 0.99,
        },
    ],
}

MOCK_CORPUS_PAYLOAD = {
    "corpus_issues": [
        {
            "issue_type": "coverage_gap",
            "severity": "P1",
            "summary": "缺少邮件客服 FAQ",
            "detail": "业务规则明确邮件客服 24 小时内回复，但知识库中没有对应独立 FAQ。",
            "affected_article_ids": [],
            "suggestion": "新增“邮件客服多久回复”标准 FAQ。",
        },
        {
            "issue_type": "coverage_gap",
            "severity": "P1",
            "summary": "缺少正确的发票标准答案",
            "detail": "现有发票 FAQ 存在错误口径，没有形成“仅支持电子发票、订单详情页申请”的标准回答。",
            "affected_article_ids": ["KB010", "KB011"],
            "suggestion": "新增一条 canonical 发票 FAQ，并合并/下线旧条目。",
        },
        {
            "issue_type": "coverage_gap",
            "severity": "P1",
            "summary": "缺少正确的快递公司标准答案",
            "detail": "现有物流 FAQ 未形成“中通、韵达、圆通，系统自动分配”的标准说法。",
            "affected_article_ids": ["KB005", "KB006", "KB027"],
            "suggestion": "新增或重写“平台使用哪些快递公司”标准 FAQ。",
        },
        {
            "issue_type": "duplicate_conflict",
            "severity": "P0",
            "summary": "退货政策 FAQ 存在重复冲突组",
            "detail": "KB001 与 KB039 问题完全相同，但答案口径明显矛盾。",
            "affected_article_ids": ["KB001", "KB039"],
            "suggestion": "只保留一条标准退货政策 FAQ，其余条目合并或删除。",
        },
        {
            "issue_type": "duplicate_conflict",
            "severity": "P0",
            "summary": "发货时效 FAQ 存在主题重复与口径分裂",
            "detail": "KB005 与 KB020 都在回答发货时效，但描述不一致，且 KB005 还混入快递公司和到货时效。",
            "affected_article_ids": ["KB005", "KB020"],
            "suggestion": "拆分发货时效与物流公司主题，统一保留标准答案。",
        },
    ],
    "governance_priorities": [
        {
            "priority": "P0",
            "theme": "先修正直接错误的业务口径",
            "reason": "退货、物流、支付、发票、会员、优惠、客服存在会直接误导用户的错误答案。",
            "recommended_action": "优先修正规则冲突类条目，必要时下线旧 FAQ。",
        },
        {
            "priority": "P0",
            "theme": "合并重复冲突 FAQ",
            "reason": "同一问题多个版本并存会导致搜索命中不稳定。",
            "recommended_action": "为退货政策、发货时效等主题建立 canonical FAQ，并清理重复条目。",
        },
        {
            "priority": "P1",
            "theme": "补齐空白答案和缺失覆盖",
            "reason": "KB032、KB037 为空，邮件客服/发票/快递标准答案存在覆盖缺口。",
            "recommended_action": "补齐高频问题答案，新增缺失 FAQ。",
        },
        {
            "priority": "P3",
            "theme": "建立高变动规则复核机制",
            "reason": "优惠、会员、客服时间、物流承诺等内容时效性强。",
            "recommended_action": "为高变动类条目增加定期复核与版本同步流程。",
        },
    ],
}


@dataclass
class Issue:
    issue_type: str
    severity: str
    summary: str
    detail: str
    impact: str = ""
    evidence: str = ""
    suggestion: str = ""
    confidence: Optional[float] = None


@dataclass
class ArticleResult:
    id: str
    question: str
    category: str
    issues: List[Issue] = field(default_factory=list)

    @property
    def has_issue(self) -> bool:
        return bool(self.issues)


@dataclass
class CorpusIssue:
    issue_type: str
    severity: str
    summary: str
    detail: str
    affected_article_ids: List[str] = field(default_factory=list)
    suggestion: str = ""


@dataclass
class GovernancePriority:
    priority: str
    theme: str
    reason: str
    recommended_action: str


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def question_signature(value: str) -> str:
    return re.sub(r"[^\w一-鿿]", "", normalize_text(value).lower())


def char_bigrams(value: str) -> set:
    normalized = question_signature(value)
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[i : i + 2] for i in range(len(normalized) - 1)}


def jaccard_similarity(left: str, right: str) -> float:
    left_set = char_bigrams(left)
    right_set = char_bigrams(right)
    if not left_set or not right_set:
        return 0.0
    union = left_set | right_set
    if not union:
        return 0.0
    return len(left_set & right_set) / len(union)


def chunked(items: List[dict], size: int) -> Iterable[List[dict]]:
    for i in range(0, len(items), max(size, 1)):
        yield items[i : i + max(size, 1)]


def short_text(value: str, limit: int = 120) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def coerce_severity(value: Any) -> str:
    text = str(value or "").upper()
    match = re.search(r"P[0-3]", text)
    if not match:
        raise ValueError(f"不支持的 severity: {value}")
    severity = match.group(0)
    if severity not in ALLOWED_SEVERITIES:
        raise ValueError(f"不支持的 severity: {value}")
    return severity


def coerce_issue_type(value: Any) -> str:
    text = str(value or "").strip()
    if text in ALLOWED_ISSUE_TYPES:
        return text
    if text in ISSUE_TYPE_ALIASES:
        return ISSUE_TYPE_ALIASES[text]
    lowered = text.lower()
    if lowered in ALLOWED_ISSUE_TYPES:
        return lowered
    if lowered in ISSUE_TYPE_ALIASES:
        return ISSUE_TYPE_ALIASES[lowered]
    raise ValueError(f"不支持的 issue_type: {value}")


def extract_json_from_response(text: str) -> dict:
    # 有些模型会包一层 ```json，这里尽量把真正的 JSON 捞出来。
    candidate = (text or "").strip()
    if not candidate:
        raise ValueError("LLM 返回为空")
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", candidate)
    if fence_match:
        candidate = fence_match.group(1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    if start < 0:
        raise ValueError("未找到 JSON 对象")
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(candidate)):
        ch = candidate[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(candidate[start : idx + 1])
    raise ValueError("无法从 LLM 返回中提取完整 JSON")


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif "content" in item:
                    parts.append(str(item.get("content", "")))
        return "".join(parts)
    return str(content or "")


def call_dashscope_chat(api_key: str, base_url: str, model: str, messages: List[dict], streaming: bool) -> str:
    # 只用标准 chat/completions，方便以后换成其他 OpenAI 兼容服务。
    payload = {
        "model": model,
        "temperature": 0,
        "stream": streaming,
        "messages": messages,
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=180) as resp:
        if not streaming:
            body = json.loads(resp.read().decode("utf-8"))
            choices = body.get("choices") or []
            if not choices:
                raise ValueError("LLM 返回缺少 choices")
            message = choices[0].get("message") or {}
            return content_to_text(message.get("content", ""))

        chunks: List[str] = []
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            event = json.loads(data)
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content is None:
                continue
            chunks.append(content_to_text(content))
        text = "".join(chunks).strip()
        if not text:
            raise ValueError("流式返回为空")
        return text


def validate_issue_dict(item: dict) -> Issue:
    issue_type = coerce_issue_type(item.get("issue_type"))
    severity = coerce_severity(item.get("severity"))
    summary = str(item.get("summary") or "").strip()
    detail = str(item.get("detail") or "").strip()
    if not summary or not detail:
        raise ValueError("issue 缺少 summary 或 detail")

    confidence_value = item.get("confidence")
    confidence: Optional[float] = None
    if confidence_value not in (None, ""):
        try:
            confidence = round(float(confidence_value), 4)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"issue confidence 非法: {confidence_value}") from exc

    return Issue(
        issue_type=issue_type,
        severity=severity,
        summary=summary,
        detail=detail,
        impact=str(item.get("impact") or "").strip(),
        evidence=str(item.get("evidence") or "").strip(),
        suggestion=str(item.get("suggestion") or "").strip(),
        confidence=confidence,
    )


def validate_article_payload(payload: dict, batch: List[dict]) -> List[ArticleResult]:
    # 条目级结果一定要和输入批次一一对齐，少一条都不行。
    items = payload.get("results")
    if not isinstance(items, list):
        raise ValueError("条目级 LLM 输出缺少 results 数组")

    article_map = {article["id"]: article for article in batch}
    results_by_id: Dict[str, ArticleResult] = {}

    for item in items:
        if not isinstance(item, dict):
            raise ValueError("results 项必须是对象")
        article_id = str(item.get("id") or "").strip()
        if article_id not in article_map:
            continue
        issues_payload = item.get("issues") or []
        if not isinstance(issues_payload, list):
            raise ValueError(f"{article_id} 的 issues 必须是数组")
        issues = [validate_issue_dict(issue) for issue in issues_payload]
        article = article_map[article_id]
        results_by_id[article_id] = ArticleResult(
            id=article_id,
            question=str(item.get("question") or article.get("question") or ""),
            category=str(item.get("category") or article.get("category") or ""),
            issues=issues,
        )

    validated = []
    for article in batch:
        validated.append(
            results_by_id.get(
                article["id"],
                ArticleResult(
                    id=article["id"],
                    question=article.get("question", ""),
                    category=article.get("category", ""),
                    issues=[],
                ),
            )
        )
    return validated


def validate_corpus_payload(payload: dict) -> Dict[str, List[Any]]:
    # 库级分析只认两块：库级问题 + 治理优先级。
    corpus_items = payload.get("corpus_issues") or []
    governance_items = payload.get("governance_priorities") or []
    if not isinstance(corpus_items, list) or not isinstance(governance_items, list):
        raise ValueError("库级 LLM 输出格式不正确")

    corpus_issues: List[CorpusIssue] = []
    for item in corpus_items:
        if not isinstance(item, dict):
            raise ValueError("corpus_issues 项必须是对象")
        summary = str(item.get("summary") or "").strip()
        detail = str(item.get("detail") or "").strip()
        if not summary or not detail:
            raise ValueError("corpus_issue 缺少 summary 或 detail")
        affected_article_ids = item.get("affected_article_ids") or []
        if not isinstance(affected_article_ids, list):
            raise ValueError("affected_article_ids 必须是数组")
        corpus_issues.append(
            CorpusIssue(
                issue_type=coerce_issue_type(item.get("issue_type")),
                severity=coerce_severity(item.get("severity")),
                summary=summary,
                detail=detail,
                affected_article_ids=[str(article_id) for article_id in affected_article_ids],
                suggestion=str(item.get("suggestion") or "").strip(),
            )
        )

    governance_priorities: List[GovernancePriority] = []
    for item in governance_items:
        if not isinstance(item, dict):
            raise ValueError("governance_priorities 项必须是对象")
        priority = coerce_severity(item.get("priority"))
        theme = str(item.get("theme") or "").strip()
        reason = str(item.get("reason") or "").strip()
        recommended_action = str(item.get("recommended_action") or "").strip()
        if not theme or not reason or not recommended_action:
            raise ValueError("治理建议缺少必要字段")
        governance_priorities.append(
            GovernancePriority(
                priority=priority,
                theme=theme,
                reason=reason,
                recommended_action=recommended_action,
            )
        )

    return {
        "corpus_issues": corpus_issues,
        "governance_priorities": governance_priorities,
    }


def build_batch_messages(batch: List[dict], taxonomy_text: str, rules_text: str) -> List[dict]:
    # 第一轮只看“单条 FAQ 本身有没有问题”。
    system_prompt = textwrap.dedent(
        """
        你是知识库治理审核代理。你的职责是依据给定的问题分类体系与业务基准文档，审核 FAQ 条目并输出严格 JSON。
        你必须遵守：
        1. 只依据 taxonomy 与 business rules 判断，不得编造规则。
        2. issue_type 只能使用以下枚举之一：
           rule_conflict, duplicate_conflict, empty_or_incomplete_answer, incomplete_info, stale_risk, coverage_gap
        3. severity 只能使用 P0, P1, P2, P3。
        4. 这里是条目级分析；coverage_gap 仅在条目本身明显体现缺口时使用，通常留给库级分析。
        5. 若规则未明确支持某个延展细节，不要武断判为 rule_conflict，可用 incomplete_info 或不标记。
        6. 只输出 JSON，不要输出解释文字或 Markdown。
        """
    ).strip()
    user_prompt = textwrap.dedent(
        f"""
        taxonomy（问题分类与分级依据）：
        {taxonomy_text}

        business_rules（业务基准文档）：
        {rules_text}

        现在请审核以下 FAQ 条目（批量输入）：
        {json.dumps(batch, ensure_ascii=False, indent=2)}

        请输出以下 JSON 结构：
        {{
          "results": [
            {{
              "id": "KB001",
              "question": "原问题文本",
              "category": "原分类",
              "has_issue": true,
              "issues": [
                {{
                  "issue_type": "rule_conflict",
                  "severity": "P0",
                  "summary": "一句话问题摘要",
                  "detail": "说明为什么有问题",
                  "impact": "这类问题对用户或业务的影响",
                  "evidence": "引用 FAQ 片段和/或规则片段",
                  "suggestion": "具体治理建议",
                  "confidence": 0.95
                }}
              ]
            }}
          ]
        }}

        要求：
        - 每个输入条目都必须在 results 中出现一次。
        - 没有问题时，issues 返回空数组，has_issue 返回 false。
        - question/category 应保持与输入一致。
        - 不要输出任何 JSON 之外的内容。
        """
    ).strip()
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_corpus_messages(snapshot: List[dict], duplicate_candidates: List[dict], pass1_results: List[ArticleResult], taxonomy_text: str, rules_text: str) -> List[dict]:
    # 第二轮拉到全库视角，看缺失覆盖、重复冲突和治理优先级。
    system_prompt = textwrap.dedent(
        """
        你是知识库治理审核代理。你的职责是站在“全库视角”识别覆盖缺失、重复冲突组、跨条目口径不一致，以及给出治理优先级。
        你必须遵守：
        1. 只依据 taxonomy、business rules、知识库快照与条目级审核结果判断。
        2. issue_type 只能使用：rule_conflict, duplicate_conflict, empty_or_incomplete_answer, incomplete_info, stale_risk, coverage_gap
        3. severity 只能使用 P0, P1, P2, P3。
        4. 只输出 JSON，不要输出解释文字或 Markdown。
        5. 如果某个判断只是候选，不要夸大为确定结论；但对明显缺口和明显重复冲突，应给出明确结论。
        """
    ).strip()
    pass1_snapshot = [
        {
            "id": item.id,
            "question": item.question,
            "category": item.category,
            "issue_types": [issue.issue_type for issue in item.issues],
            "severities": [issue.severity for issue in item.issues],
        }
        for item in pass1_results
        if item.issues
    ]
    user_prompt = textwrap.dedent(
        f"""
        taxonomy（问题分类与分级依据）：
        {taxonomy_text}

        business_rules（业务基准文档）：
        {rules_text}

        知识库快照：
        {json.dumps(snapshot, ensure_ascii=False, indent=2)}

        条目级审核结果概览：
        {json.dumps(pass1_snapshot, ensure_ascii=False, indent=2)}

        候选重复/近似问题组（仅作参考，不代表最终结论）：
        {json.dumps(duplicate_candidates, ensure_ascii=False, indent=2)}

        请输出以下 JSON：
        {{
          "corpus_issues": [
            {{
              "issue_type": "coverage_gap",
              "severity": "P1",
              "summary": "一句话问题摘要",
              "detail": "说明为什么这是库级问题",
              "affected_article_ids": ["KB010", "KB011"],
              "suggestion": "针对库级问题的治理动作"
            }}
          ],
          "governance_priorities": [
            {{
              "priority": "P0",
              "theme": "治理主题",
              "reason": "为什么优先做",
              "recommended_action": "推荐动作"
            }}
          ]
        }}

        要求：
        - 覆盖缺失应识别“规则已明确但知识库缺少对应 FAQ 或缺少标准答案”的情况。
        - 重复冲突应识别“同一问题多个版本并存且答案不一致”的情况。
        - governance_priorities 要按业务可执行性给出，最多 5 条。
        - 不要输出任何 JSON 之外的内容。
        """
    ).strip()
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def mock_analyze_articles_batch(batch: List[dict]) -> dict:
    results = []
    for article in batch:
        issues = MOCK_ARTICLE_ISSUES.get(article["id"], [])
        results.append(
            {
                "id": article["id"],
                "question": article.get("question", ""),
                "category": article.get("category", ""),
                "has_issue": bool(issues),
                "issues": issues,
            }
        )
    return {"results": results}


def mock_analyze_corpus() -> dict:
    return MOCK_CORPUS_PAYLOAD


def analyze_articles_batch_llm(batch: List[dict], taxonomy_text: str, rules_text: str, api_key: str, base_url: str, model: str, streaming: bool) -> List[ArticleResult]:
    messages = build_batch_messages(batch=batch, taxonomy_text=taxonomy_text, rules_text=rules_text)
    raw_text = call_dashscope_chat(api_key=api_key, base_url=base_url, model=model, messages=messages, streaming=streaming)
    payload = extract_json_from_response(raw_text)
    return validate_article_payload(payload, batch)


def analyze_corpus_llm(snapshot: List[dict], duplicate_candidates: List[dict], pass1_results: List[ArticleResult], taxonomy_text: str, rules_text: str, api_key: str, base_url: str, model: str, streaming: bool) -> Dict[str, List[Any]]:
    messages = build_corpus_messages(
        snapshot=snapshot,
        duplicate_candidates=duplicate_candidates,
        pass1_results=pass1_results,
        taxonomy_text=taxonomy_text,
        rules_text=rules_text,
    )
    raw_text = call_dashscope_chat(api_key=api_key, base_url=base_url, model=model, messages=messages, streaming=streaming)
    payload = extract_json_from_response(raw_text)
    return validate_corpus_payload(payload)


def find_duplicate_candidates(articles: List[dict]) -> List[dict]:
    # 这里先用一个偏保守的候选集，真正是不是重复冲突再交给后面的库级分析判断。
    exact_groups: Dict[str, List[str]] = defaultdict(list)
    article_map = {article["id"]: article for article in articles}
    for article in articles:
        exact_groups[question_signature(article.get("question", ""))].append(article["id"])

    candidates: List[dict] = []
    seen_keys = set()
    for article_ids in exact_groups.values():
        if len(article_ids) > 1:
            key = tuple(sorted(article_ids))
            seen_keys.add(key)
            candidates.append(
                {
                    "article_ids": list(key),
                    "reason": "exact_question_match",
                    "questions": [article_map[article_id].get("question", "") for article_id in key],
                }
            )

    for i, left in enumerate(articles):
        for right in articles[i + 1 :]:
            if left.get("category") != right.get("category"):
                continue
            similarity = jaccard_similarity(left.get("question", ""), right.get("question", ""))
            if similarity < 0.45:
                continue
            key = tuple(sorted([left["id"], right["id"]]))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            candidates.append(
                {
                    "article_ids": list(key),
                    "reason": "same_category_question_similarity",
                    "similarity": round(similarity, 3),
                    "questions": [left.get("question", ""), right.get("question", "")],
                }
            )
    return candidates


def build_corpus_snapshot(articles: List[dict], pass1_results: List[ArticleResult]) -> List[dict]:
    issues_by_id = {result.id: result for result in pass1_results}
    snapshot = []
    for article in articles:
        result = issues_by_id.get(article["id"])
        snapshot.append(
            {
                "id": article["id"],
                "question": article.get("question", ""),
                "category": article.get("category", ""),
                "answer_preview": short_text(article.get("answer", ""), 140),
                "updated_at": article.get("updated_at", ""),
                "pass1_issue_types": [issue.issue_type for issue in (result.issues if result else [])],
            }
        )
    return snapshot


def summarize(results: List[ArticleResult], corpus_issues: List[CorpusIssue], governance_priorities: List[GovernancePriority]) -> dict:
    # 这里做的是报告摘要统计，方便业务方先看全局，再看明细。
    issue_type_counts: Dict[str, int] = defaultdict(int)
    severity_counts: Dict[str, int] = defaultdict(int)
    flagged_articles = 0

    for result in results:
        if result.issues:
            flagged_articles += 1
        for issue in result.issues:
            issue_type_counts[issue.issue_type] += 1
            severity_counts[issue.severity] += 1

    for issue in corpus_issues:
        issue_type_counts[issue.issue_type] += 1
        severity_counts[issue.severity] += 1

    top_priorities = [priority.theme for priority in governance_priorities[:3]]
    return {
        "flagged_articles": flagged_articles,
        "issue_type_counts": dict(sorted(issue_type_counts.items())),
        "severity_counts": dict(sorted(severity_counts.items())),
        "corpus_issue_count": len(corpus_issues),
        "top_priorities": top_priorities,
    }


def render_markdown(report: dict) -> str:
    # Markdown 版本主要是给人直接阅读；JSON 版本更适合后续接系统。
    lines = []
    lines.append("# 知识库治理检测报告")
    lines.append("")
    lines.append("## 摘要结论")
    lines.append(f"- 检测模式：{report['mode']}")
    lines.append(f"- 提供方：{report['provider']}")
    lines.append(f"- 模型：{report['model']}")
    lines.append(f"- 知识库总条目数：{report['total_articles']}")
    lines.append(f"- 标记为有问题的条目数：{report['summary']['flagged_articles']}")
    lines.append(f"- 库级问题数：{report['summary']['corpus_issue_count']}")
    if report["summary"].get("top_priorities"):
        lines.append(f"- 当前优先治理主题：{'；'.join(report['summary']['top_priorities'])}")
    lines.append("")

    lines.append("## 问题类型分布")
    for key, value in report["summary"]["issue_type_counts"].items():
        lines.append(f"- {ISSUE_TYPE_LABELS.get(key, key)}（{key}）：{value}")
    lines.append("")

    lines.append("## 严重程度分布")
    for key, value in report["summary"]["severity_counts"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")

    if report.get("governance_priorities"):
        lines.append("## 高优先级治理建议")
        for item in report["governance_priorities"]:
            lines.append(f"- [{item['priority']}] {item['theme']}：{item['reason']}；建议动作：{item['recommended_action']}")
        lines.append("")

    lines.append("## 条目问题明细")
    has_any_detail = False
    for item in report["results"]:
        if not item["issues"]:
            continue
        has_any_detail = True
        lines.append("")
        lines.append(f"### {item['id']} - {item['question']}")
        lines.append(f"- 类别：{item['category']}")
        for issue in item["issues"]:
            lines.append(f"- [{issue['severity']}] {ISSUE_TYPE_LABELS.get(issue['issue_type'], issue['issue_type'])}：{issue['summary']}")
            lines.append(f"  - 说明：{issue['detail']}")
            if issue.get("impact"):
                lines.append(f"  - 影响：{issue['impact']}")
            if issue.get("evidence"):
                lines.append(f"  - 证据：{issue['evidence']}")
            if issue.get("suggestion"):
                lines.append(f"  - 建议：{issue['suggestion']}")
    if not has_any_detail:
        lines.append("")
        lines.append("- 本次未识别到条目级问题。")

    if report.get("corpus_issues"):
        lines.append("")
        lines.append("## 库级问题")
        for issue in report["corpus_issues"]:
            affected = issue.get("affected_article_ids") or []
            affected_text = f"；涉及条目：{', '.join(affected)}" if affected else ""
            lines.append(f"- [{issue['severity']}] {ISSUE_TYPE_LABELS.get(issue['issue_type'], issue['issue_type'])}：{issue['summary']}；{issue['detail']}{affected_text}")
            if issue.get("suggestion"):
                lines.append(f"  - 建议：{issue['suggestion']}")

    return "\n".join(lines)


def build_report(kb_path: str, rules_path: str, taxonomy_path: str, mode: str, model: str, api_key: Optional[str], base_url: str, streaming: bool, batch_size: int) -> dict:
    # 整个脚本的主流程基本都在这里：读文件、跑两轮分析、最后组装报告。
    kb_path = os.path.abspath(kb_path)
    rules_path = os.path.abspath(rules_path)
    taxonomy_path = os.path.abspath(taxonomy_path)

    articles = read_json(kb_path)
    rules_text = read_text(rules_path)
    taxonomy_text = read_text(taxonomy_path)

    if mode == "llm" and not api_key:
        raise ValueError("LLM 模式需要提供 API Key，可通过 --api-key 或环境变量 DASHSCOPE_API_KEY 传入。")

    results: List[ArticleResult] = []
    for batch in chunked(articles, batch_size):
        if mode == "mock":
            payload = mock_analyze_articles_batch(batch)
            results.extend(validate_article_payload(payload, batch))
        else:
            results.extend(
                analyze_articles_batch_llm(
                    batch=batch,
                    taxonomy_text=taxonomy_text,
                    rules_text=rules_text,
                    api_key=api_key or "",
                    base_url=base_url,
                    model=model,
                    streaming=streaming,
                )
            )

    duplicate_candidates = find_duplicate_candidates(articles)
    corpus_snapshot = build_corpus_snapshot(articles, results)
    if mode == "mock":
        corpus_payload = mock_analyze_corpus()
        corpus_result = validate_corpus_payload(corpus_payload)
    else:
        corpus_result = analyze_corpus_llm(
            snapshot=corpus_snapshot,
            duplicate_candidates=duplicate_candidates,
            pass1_results=results,
            taxonomy_text=taxonomy_text,
            rules_text=rules_text,
            api_key=api_key or "",
            base_url=base_url,
            model=model,
            streaming=streaming,
        )

    corpus_issues: List[CorpusIssue] = corpus_result["corpus_issues"]
    governance_priorities: List[GovernancePriority] = corpus_result["governance_priorities"]

    report = {
        "mode": mode,
        "provider": "dashscope" if mode == "llm" else "mock",
        "model": model,
        "rules_file": rules_path,
        "taxonomy_file": taxonomy_path,
        "kb_file": kb_path,
        "total_articles": len(articles),
        "summary": summarize(results, corpus_issues, governance_priorities),
        "results": [
            {
                "id": item.id,
                "question": item.question,
                "category": item.category,
                "has_issue": item.has_issue,
                "issues": [asdict(issue) for issue in item.issues],
            }
            for item in results
        ],
        "corpus_issues": [asdict(issue) for issue in corpus_issues],
        "governance_priorities": [asdict(item) for item in governance_priorities],
        "duplicate_candidates": duplicate_candidates,
    }
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="扫描知识库条目并输出基于 LLM 的治理报告")
    parser.add_argument("--kb", default=os.path.join(SCRIPT_DIR, "task6_kb_articles.json"), help="知识库 JSON 文件路径")
    parser.add_argument("--rules", default=os.path.join(SCRIPT_DIR, "task6_business_context.md"), help="业务规则 Markdown 文件路径")
    parser.add_argument("--taxonomy", default=os.path.join(SCRIPT_DIR, "leixing.txt"), help="问题分类说明文件路径")
    parser.add_argument("--mode", choices=["llm", "mock"], default="llm", help="检测模式：llm 调用 DashScope，mock 模拟结构化结果")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="LLM 模式使用的模型名")
    parser.add_argument("--api-key", default=os.getenv("DASHSCOPE_API_KEY"), help="DashScope API Key；不传则读取环境变量 DASHSCOPE_API_KEY")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="DashScope OpenAI-compatible base URL")
    parser.add_argument("--format", choices=["json", "markdown"], default="json", help="输出格式")
    parser.add_argument("--output", help="输出文件路径；不传则打印到标准输出")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="条目级批量审核的 batch 大小")
    parser.add_argument("--no-streaming", action="store_true", help="关闭流式返回")
    return parser.parse_args()


def main() -> int:
    # main 只做参数解析、异常兜底和最终输出，具体逻辑都放在 build_report 里。
    args = parse_args()
    try:
        report = build_report(
            kb_path=args.kb,
            rules_path=args.rules,
            taxonomy_path=args.taxonomy,
            mode=args.mode,
            model=args.model,
            api_key=args.api_key,
            base_url=args.base_url,
            streaming=not args.no_streaming if DEFAULT_STREAMING else False,
            batch_size=args.batch_size,
        )
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    output_text = json.dumps(report, ensure_ascii=False, indent=2) if args.format == "json" else render_markdown(report)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_text)
    else:
        print(output_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
