"""CLI 入口 — python -m marketing"""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="marketing",
        description="AI Reader V2 — AI 自主营销系统",
    )
    sub = parser.add_subparsers(dest="command")

    # 选题
    pick = sub.add_parser("pick", help="热门小说自动选题")
    pick.add_argument("--top", type=int, default=5, help="推荐数量")
    pick.add_argument("--confirm", type=int, help="确认第 N 个选题")

    # 分析
    analyze = sub.add_parser("analyze", help="自动分析选定小说")
    analyze.add_argument("--retry", action="store_true", help="重试失败的分析")

    # 截图
    sub.add_parser("screenshot", help="分析结果自动截图")

    # 文案
    gen = sub.add_parser("generate", help="生成多平台文案")
    gen.add_argument("--platform", choices=["xiaohongshu", "juejin", "zhihu", "twitter"])
    gen.add_argument("--angle", choices=["visual", "pain", "trivia", "contrast", "quiz"])

    # 审批
    review = sub.add_parser("review", help="审批待发布内容")
    review.add_argument("action", nargs="?", choices=["approve", "reject", "edit"])
    review.add_argument("content_id", nargs="?", type=int)

    # 发布
    pub = sub.add_parser("publish", help="发布内容到平台")
    pub.add_argument("--platform", help="目标平台")
    pub.add_argument("--content-id", type=int, help="内容 ID")
    pub.add_argument("--dry-run", action="store_true", help="模拟发布")
    pub.add_argument("--cron", action="store_true", help="定时发布模式")

    # 仪表盘
    dash = sub.add_parser("dashboard", help="效果追踪仪表盘")
    dash.add_argument("--collect", action="store_true", help="采集互动数据")
    dash.add_argument("--summary", action="store_true", help="效果汇总")
    dash.add_argument("--conversions", action="store_true", help="转化漏斗")
    dash.add_argument("--logs", action="store_true", help="Pipeline 运行日志")
    dash.add_argument("--alerts", action="store_true", help="查看预警")
    dash.add_argument("--detail", help="运行详情 (run_id)")

    # Pipeline 一键运行
    run = sub.add_parser("run", help="一键运行完整 Pipeline")
    run.add_argument("--novel", help="指定小说名或 ID")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # 延迟导入避免启动慢
    import asyncio

    if args.command == "pick":
        from marketing.novel_selector import run_selector
        asyncio.run(run_selector(top=args.top, confirm=args.confirm))
    elif args.command == "analyze":
        from marketing.auto_analyzer import run_analyzer
        asyncio.run(run_analyzer(retry=args.retry))
    elif args.command == "screenshot":
        from marketing.screenshot_bot import run_screenshots
        asyncio.run(run_screenshots())
    elif args.command == "generate":
        from marketing.content_generator import run_generator
        asyncio.run(run_generator(platform=args.platform, angle=args.angle))
    elif args.command == "publish":
        from marketing.publisher import run_publisher
        asyncio.run(run_publisher(
            platform=args.platform,
            content_id=args.content_id,
            dry_run=args.dry_run,
            cron=args.cron,
        ))
    elif args.command == "review":
        from marketing.publisher import run_review
        asyncio.run(run_review(action=args.action, content_id=args.content_id))
    elif args.command == "dashboard":
        from marketing.dashboard import run_dashboard
        asyncio.run(run_dashboard(
            collect=args.collect,
            summary=args.summary,
            conversions=args.conversions,
            logs=args.logs,
            alerts=args.alerts,
            detail=args.detail,
        ))
    elif args.command == "run":
        from marketing.pipeline import run_full_pipeline
        asyncio.run(run_full_pipeline(novel=args.novel))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
