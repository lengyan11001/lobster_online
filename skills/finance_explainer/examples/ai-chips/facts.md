# 事实核查表：中国买不到顶级显卡，为什么还是世界第二 AI 大国？

核查日期 2026-10-21。⚠ = 未能直接核实原始来源，或只能通过转述 / 二手来源核实。

## 1. 美国对华 AI 芯片出口管制时间线

- 美国商务部工业与安全局（BIS）发布先进计算与半导体制造出口管制新规，限制中国获取高端 AI 芯片和制造设备。部分条款 10 月 12 日生效，其余 10 月 21 日生效 — 2022-10-07 — BIS 新闻稿（经 Wikipedia 引用）https://www.bis.doc.gov/index.php/documents/about-bis/newsroom/press-releases/3158-2022-10-07-bis-press-release-advanced-computing-and-semiconductor-manufacturing-controls-final/file ；条目 https://en.wikipedia.org/wiki/United_States_New_Export_Controls_on_Advanced_Computing_and_Semiconductors_to_China
- 2022 年规则之后 H100 被禁售，英伟达改卖降速版 H800/A800。2023 年的更新把这个口子堵上：英伟达向 SEC 披露，A100、A800、H100、H800、L40、L40S、RTX 4090 都受限 — 2023-10-17 — CNBC https://www.cnbc.com/2023/10/17/us-bans-export-of-more-ai-chips-including-nvidia-h800-to-china.html
- 新增管制：24 类半导体制造设备、3 类芯片设计/制造软件、高带宽内存（HBM），另有 140 个实体列入实体清单 — 2024-12-02 — BIS 新闻稿 https://www.bis.gov/press-release/commerce-strengthens-export-controls-restrict-chinas-capability-produce-advanced-semiconductors-military
- 美国政府于 4 月 9 日通知英伟达，H20 出口中国须申请许可证。英伟达随后宣布计提约 55 亿美元季度费用 — 2025-04-15 — CNBC https://www.cnbc.com/2025/04/15/nvidia-says-it-will-record-5point5-billion-quarterly-charge-tied-to-h20-processors-exported-to-china.html
- 英伟达称已获美国政府保证会发放许可证，H20「很快」恢复对华交付 — 2025-07-15 — CNBC https://www.cnbc.com/2025/07/15/nvidia-says-us-government-will-allow-it-to-resume-h20-ai-chip-sales-to-china.html
- 英伟达、AMD 同意把在华 H20 / MI308 销售收入的 15% 交给美国政府，以此换取出口许可 — 2025-08-11 — BBC https://www.bbc.com/news/articles/cvgvvnx8y19o
- 中方反应：据彭博报道，中国当局要求企业避免使用 H20，政府和涉及国家安全的场景尤其如此 — 2025-08-12 — CNBC https://www.cnbc.com/2025/08/12/china-warns-companies-against-using-nvidia-and-amd-chips-report-says.html 。之后英伟达要求供应商暂停 H20 生产 — 2025-08-21/22 — Reuters（经 Wikipedia 引用）https://www.reuters.com/world/china/nvidia-orders-halt-h20-chip-production-after-china-directive-information-says-2025-08-22/
- 据英国《金融时报》报道，国家网信办要求字节、阿里等停止测试和订购英伟达特供版 RTX Pro 6000D，黄仁勋表示「失望」 — 2025-09-17 — CNBC https://www.cnbc.com/2025/09/17/nvidia-ceo-disappointed-after-reports-china-has-banned-its-ai-chips.html
- ⚠ 「2025 年 7 月 31 日网信办约谈英伟达、询问 H20 后门风险」：没有抓到原始来源，只能确认 8 月、9 月的上述报道。
- 特朗普宣布允许 H200 卖给中国「经批准的客户」，条件是美国抽成 25% — 2025-12-08 — CNBC https://www.cnbc.com/2025/12/08/trump-nvidia-h200-sales-china.html
- 商务部 1 月 13 日发布新规，把放宽写进条文，适用于 H200、AMD MI325X 及同级芯片。芯片须先运到美国做第三方检测，过境时征 25% 关税 — 2026-01-13/14 — CFR https://www.cfr.org/articles/new-ai-chip-export-policy-china-strategically-incoherent-and-unenforceable ；BBC https://www.bbc.com/news/articles/cg4erx1n04lo ；Guardian https://www.theguardian.com/technology/2026/jan/17/china-blocks-nvidia-h200-ai-chips-that-us-government-cleared-for-export-report
- 据报道，中国海关拒绝 H200 入境，部分供应商暂停相关零部件生产。最先进的 Blackwell 仍禁止对华销售 — 2026-01-17 — Guardian（同上链接）
- 最新状态：据 Reuters 报道，美国商务部批准阿里、腾讯、字节、京东等 10 家中国企业，以及联想、富士康等经销商采购 H200 — 2026-05-14 — Reuters（经 Wikipedia 引用，Reuters 原文被拦截 ⚠）https://www.reuters.com/business/retail-consumer/us-clears-h200-chip-sales-10-china-firms-nvidia-ceo-looks-breakthrough-2026-05-14 。⚠ 此后是否真正大规模交付，没有查到可靠来源。

## 2. 「世界第二」的证据

- 2024 年「重要 AI 模型」数量：美国 40 个，中国 15 个，欧洲 3 个 — 2025 年 4 月 — Stanford HAI《AI Index 2025》https://hai.stanford.edu/ai-index/2025-ai-index-report
- 中美头部模型差距：2023 年底，MMLU / MMMU / MATH / HumanEval 四项分别差 17.5 / 13.5 / 24.3 / 31.6 个百分点；到 2024 年底缩小到 0.3 / 8.1 / 1.6 / 3.7 — AI Index 2025 技术性能章 https://hai.stanford.edu/ai-index/2025-ai-index-report/technical-performance
- 2023 年中国 AI 专利授权量占全球 69.7% — AI Index 2025 研发章 https://hai.stanford.edu/ai-index/2025-ai-index-report/research-and-development
- 2025 年重要模型：美国 59 个，中国 35 个。中国在论文数量、引用和专利授权上领先；全球被引最多的前 100 篇 AI 论文中，中国从 2021 年的 33 篇升到 2024 年的 41 篇 — AI Index 2026 研发章 https://hai.stanford.edu/ai-index/2026-ai-index-report/research-and-development
- 「中美模型性能差距已基本消失」：2025 年 2 月 DeepSeek-R1 一度追平美国最强模型；截至 2026 年 3 月，美国最强模型（Anthropic）只领先 2.7%（Arena 分数 1503 对 1464） — AI Index 2026 https://hai.stanford.edu/ai-index/2026-ai-index-report
- 投资差距：2025 年美国私人 AI 投资 2859 亿美元，是中国 124 亿美元的 23 倍以上。HAI 注明这可能低估了中国的投入，因为没算政府引导基金 — AI Index 2026（同上）
- 斯坦福 Global AI Vibrancy 排名：美国第一、中国第二、英国第三，但中国是「遥远的第二」。2023 年美国私人投资 672 亿美元，中国 78 亿；重要模型美国 61 个，中国 15 个 — 2024 年 11 月 — Stanford HAI https://hai.stanford.edu/news/global-ai-power-rankings-stanford-hai-tool-ranks-36-countries-in-ai

## 3. DeepSeek

- DeepSeek-V3 在 2048 块 H800 上训练，完整训练共 278.8 万 H800 GPU 小时（预训练 266.4 万、长上下文扩展 11.9 万、后训练 0.5 万）。按每 GPU 小时 2 美元租金计算，总成本 557.6 万美元。论文原文注明，这个数字不包括此前的研究和消融实验 — 2024-12-27（v1）— arXiv:2412.19437 https://arxiv.org/abs/2412.19437
- DeepSeek-R1 论文上线 arXiv — 2025-01-22 — https://arxiv.org/abs/2501.12948
- 英伟达单日市值蒸发近 6000 亿美元，股价跌 17%，收于 118.58 美元，是美股史上单家公司最大单日市值损失 — 2025-01-27 — CNBC https://www.cnbc.com/2025/01/27/nvidia-sheds-almost-600-billion-in-market-cap-biggest-drop-ever.html
- 截至 2022 年，幻方「萤火二号」集群有 5000 块 PCIe A100 — Wikipedia「DeepSeek」词条（引用幻方官网）https://en.wikipedia.org/wiki/DeepSeek ⚠ 二手来源

## 4. 开源生态

- 基于 Hugging Face 2020-06 至 2025-08 完整下载数据：最近一年（2024-08 至 2025-08）中国模型占下载量 17.1%，首次超过美国（15.7%~15.8%）；DeepSeek 和 Qwen 两家合计占 14% — 2025-11-27 — arXiv:2512.03073《Economies of Open Intelligence》https://arxiv.org/abs/2512.03073
- Hugging Face 官方博客：在新发布（不满 1 年）的模型中，中国模型的下载量超过包括美国在内的任何国家；R1 成为 Hugging Face 史上点赞最多的模型；百度在 HF 上的发布数从 2024 年的 0 个增加到 2025 年的 100 多个 — 2026-01-20 — https://huggingface.co/blog/huggingface/one-year-since-the-deepseek-moment
- ⚠ 没有找到可靠来源的「Qwen 衍生模型总数」（流传的「17 万 / 18 万+」等数字未核实）。HF 博客只说 Qwen1.5-0.5B 是 Qwen 系列中衍生模型最多的。

## 5. 国产芯片

- 华为：昇腾 910C 随 Atlas 900 A3 超节点在 2025 年规模部署。一个超节点最多 384 颗 910C，算力最高 300 PFLOPS（CloudMatrix384 是基于它的云实例），已部署 300 多套。路线图：昇腾 950PR 2026 年一季度上市；Atlas 950 超节点含 8192 颗芯片，2026 年四季度上市；之后是 960、970 — 2025-09-18 — 华为全联接大会徐直军主题演讲 https://www.huawei.com/en/news/2025/9/hc-xu-keynote-speech
- 一颗 910C 由两颗 910B 裸片封装而成。据报道，DeepSeek 评估认为 910C 推理性能约为 H100 的 60%。CSIS 的信源称，华为通过空壳公司从台积电获得 200 多万颗 AI 芯片裸片，并囤了超过一年用量的 HBM — 2025-03-07 — CSIS https://www.csis.org/analysis/deepseek-huawei-export-controls-and-future-us-china-ai-race
- 中芯国际：2023 年 Mate60 Pro 的麒麟 9000S 由中芯 N+2（7nm 级）工艺制造 — 2023-10-06 — CSIS https://www.csis.org/analysis/chip-race-china-gives-huawei-steering-wheel-huaweis-new-smartphone-and-future 。⚠ 「中芯拿不到 EUV、只能用 DUV 多重曝光做 7nm/5nm 级」是业内共识，但本次没有抓到原始来源。
- 寒武纪 2025 年营收 64.97 亿元，同比增长 453.21%；归母净利润 20.59 亿元，上市后首次全年盈利（2024 年亏损 4.52 亿元）；云端产品收入 64.76 亿元 — 2026-03-12 — MemoryMarket 转述年报 https://www.memorymarket.com/a/14657 （原始年报未直接抓取）
- ⚠ 据彭博转述摩根士丹利估算，中国 AI 芯片自给率 2025 年约 41%（2020 年为 10%），预计 2030 年达 86% — 2026-05-12 — Bloomberg（付费墙，经 Wikipedia 引用）https://www.bloomberg.com/news/articles/2026-05-12/china-earns-500-million-per-hour-from-export-supercharged-by-ai
- 截至 2025 年，我国智能算力规模超过 1590 EFLOPS（「1590 百亿亿次/秒」） — 2026-03-03 — 中国政府网转人民日报 https://www.gov.cn/yaowen/liebiao/202603/content_7060221.htm

## 6. 其他要素：电力、人才、应用规模

- 电力：2025 年中国用电量 10,573 TWh，约占全球三分之一（2015 年为 5,802 TWh）；当年新增光伏发电 336 TWh — 2026 — Ember《Global Electricity Review 2026》https://ember-energy.org/latest-insights/global-electricity-review-2026/major-countries-and-regions
- 美国 2025 年用电量增长 3%（+131 TWh），由此推算全年约 4,400~4,500 TWh（Ember 原文没有直接给总量，⚠ 系推算）。数据中心预计贡献 2030 年前美国约一半的用电增长 — 同上
- ⚠ 「中国数据中心电价比美国低多少」没有找到可靠的量化来源，不建议引用具体数字。
- 人才：在 NeurIPS 2022 顶级研究者中，本科在中国读的占 47%（2019 年为 29%） — MacroPolo 全球 AI 人才追踪 2023 版（Wayback 存档 2025-02-28）https://web.archive.org/web/20250228092334/https://archivemacropolo.org/interactive/digital-projects/the-global-ai-talent-tracker/ 。⚠ 该存档站当前的线上页面已改成标注「3.0」的另一套数字（38% 等），和原版矛盾，来源存疑，不要引用。
- 应用规模：截至 2025 年 6 月，我国生成式 AI 用户 5.15 亿，普及率 36.5% — 2025-10-18 — 中国政府网 / CNNIC https://www.gov.cn/yaowen/liebiao/202510/content_7044952.htm ；截至 2025 年 12 月为 6.02 亿，普及率 42.8% — 2026-03-03 — 中国政府网 https://www.gov.cn/yaowen/liebiao/202603/content_7060221.htm 。新华社等报道截至 2026 年上半年已突破 7 亿、普及率超 50%（CNNIC 2026-09-29 报告）⚠ 新华网原页被拦截，只看到了搜索摘要。

## 7. 绕道手段与约束

- 走私：美国司法部起诉 Supermicro 联合创始人 Wally Liaw 等 3 人。指控称 2024-2025 年间经中间公司采购约 25 亿美元服务器转运中国，其中 2025 年 4 月下旬到 5 月中旬至少约 5.1 亿美元 — 2026-03-19 — DOJ https://www.justice.gov/opa/pr/three-charged-conspiring-unlawfully-divert-cutting-edge-us-artificial-intelligence
- 台湾起诉 9 人，包括一名英伟达高级经理：130 台 B300 服务器中 74 台已运抵中国客户 — 2026-08-24 — Ars Technica https://www.arstechnica.com/tech-policy/2026/08/nvidia-senior-manager-linked-to-supermicro-scheme-smuggling-ai-servers-to-china/ ；另有一案疑经日本走私，扣押约 50 台服务器 — 2026-05-27 — Straits Times https://www.straitstimes.com/asia/east-asia/taiwan-said-to-suspect-nvidia-chips-smuggled-to-china-via-japan
- 据 CSIS 报道，有媒体采访了 8 个 H100 走私网络，每个都声称完成过超过 1 亿美元的交易；新加坡是英伟达 18% 营收的开票地，但只占 2% 的发货地 — 2025-03-07 — CSIS（同上）
- 租用海外云：中国企业可以向境外云厂商租用 GPU 算力，不违反出口管制（例如字节在 AWS 上跑推理） — 2026-01-28 — Lawfare https://www.lawfaremedia.org/article/trump-s-illegal-ai-chip-export-controls--and-who-can-challenge-them （引用 RAND 2025-08）
- 算力差距：按 AI 超算性能计算，美国约占全球 75%，中国约占 15%（数据集只覆盖全球约 10%~20%） — 2025-04-23 / 2025-06-05 — Epoch AI https://epoch.ai/blog/trends-in-ai-supercomputers ；https://epoch.ai/data-insights/ai-supercomputers-performance-share-by-country
- 但在公共 / 公私合营 AI 超算的数量上，中国 2025 年以 85 台居首 — AI Index 2026 https://hai.stanford.edu/ai-index/2026-ai-index-report

## 容易讲错的地方

- 「买不到顶级显卡」≠「没有 GPU」。中国合法买过 A800/H800/H20，存量有 A100/H100；2026 年起 H200 有条件放开（但中国海关一度拦截）。真正买不到的是 Blackwell 这一代最新卡。
- DeepSeek 的 557.6 万美元只是最终一次训练的租金折算，不含前期研究、消融实验、人员和集群购置成本，不能说「600 万美元做出 GPT-4」。论文本身写得很清楚。
- 英伟达那天跌了「近 6000 亿美元」（CNBC 的说法），常见的 5890 亿 / 5930 亿是不同媒体的计算口径。
- 「性能差距消失」说的是 Arena 等基准分数的头部差距（2026 年 3 月为 2.7%），不代表算力、投资和模型数量也追平了：美国重要模型 59 对 35，私人投资 23 倍以上，算力约 75% 对 15%。
- 论文和专利「数量」中国领先，但 HAI 也指出，高影响力专利和重要模型美国仍领先。
- 开源下载份额 17.1% 对 15.7% 是 HF 一年滚动窗口里的「国家份额」，不是「中国占开源一半」。
- 15% 分成（H20，2025 年 8 月）和 25%（H200，2025 年 12 月宣布，2026 年 1 月落地为过境关税）是两件事，别混在一起。
- 中国的约束有一部分是自己设的：网信办叫停 RTX Pro 6000D 采购、劝阻 H20、海关拦截 H200，目的是扶持国产芯片。
- 人才数据要用 MacroPolo 原版的 47%（NeurIPS 2022），不要用现在线上「3.0」页面的 38%。
