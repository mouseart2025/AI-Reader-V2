# 章节切分测试与改进工作流

## 快速命令

```bash
# 1. 运行单元测试（快速，合成文本）
cd backend && uv run pytest tests/test_chapter_splitter.py tests/test_chapter_classifier.py -v

# 2. 运行回归测试（需要样本库）
cd backend && EBOOK_SAMPLE_DIR=/path/to/ebooks uv run pytest tests/test_split_regression.py -v

# 3. 批量分析（全量 1569 本）
cd backend && EBOOK_SAMPLE_DIR=/path/to/ebooks uv run python ../scripts/analyze_splits.py --output ../scripts/split_analysis_report.json

# 4. 批量分析（仅前 50 本，快速验证）
cd backend && EBOOK_SAMPLE_DIR=/path/to/ebooks uv run python ../scripts/analyze_splits.py --sample 50

# 5. 仅输出问题文件
cd backend && EBOOK_SAMPLE_DIR=/path/to/ebooks uv run python ../scripts/analyze_splits.py --problems-only

# 6. 标注 ground truth（从报告中抽样）
cd backend && uv run python ../scripts/generate_ground_truth.py --from-report ../scripts/split_analysis_report.json --dir /path/to/ebooks

# 7. 标注 ground truth（指定文件）
cd backend && uv run python ../scripts/generate_ground_truth.py --files /path/to/book1.txt /path/to/book2.txt
```

## 改进循环

1. **运行批量分析** → 生成 `split_analysis_report.json`
2. **审阅报告** → 查看 `problem_distribution` 和 top 问题文件
3. **如发现新模式** → 在 `chapter_splitter.py` 中添加/修改正则
4. **添加单元测试** → 覆盖新模式到 `test_chapter_splitter.py`
5. **运行回归测试** → 确保不退步
6. **如有新样本** → 使用 `generate_ground_truth.py` 标注
7. **更新 baseline.json**
8. **重复**

## 问题分类说明

| 分类 | 含义 |
|------|------|
| `ok` | 切分正常 |
| `no_heading_match` | 未检测到标准章节标题，使用了 fixed_size fallback |
| `fallback_used` | 使用了启发式标题检测 |
| `many_tiny_chapters` | 超过 20% 的章节 < 200 字（可能误切） |
| `high_variance` | 章节大小变异系数 > 2.0 |
| `heading_too_dense` | 章节过多过短（章均 < 500 字） |
| `heading_too_sparse` | 章节太少（< 5 章但 > 100K 字） |
| `single_huge_chapter` | 单章 > 30K 字 |

## 文件结构

```
scripts/
├── analyze_splits.py              # 批量分析脚本
├── generate_ground_truth.py       # 交互式标注工具
├── split_ground_truth/
│   └── baseline.json              # 标注数据（回归测试读取）
├── split_analysis_report.json     # 最新分析报告
└── README-split-testing.md        # 本文档

backend/tests/
├── test_chapter_splitter.py       # 单元测试（29 个）
├── test_chapter_classifier.py     # 单元测试（12 个）
└── test_split_regression.py       # 回归测试（从 baseline.json 动态生成）
```

## 如何添加新 Ground Truth

1. 运行 `generate_ground_truth.py`
2. 工具会展示当前引擎的切分结果
3. 判断：`Y`（正确）/ `N`（有误）/ `S`（跳过）/ `C`（正确不切分）
4. 标注自动保存到 `baseline.json`
5. 运行回归测试验证
