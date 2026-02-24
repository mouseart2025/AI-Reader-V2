"""Settings and environment health-check endpoints."""

import asyncio
import json
import os
import platform
import shutil
import subprocess
import time

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.infra.config import (
    CONTEXT_WINDOW_SIZE,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_PROVIDER,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    get_model_name,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

REQUIRED_MODEL = "qwen2.5:7b"

# ── Model recommendation catalog ──────────────────

MODEL_CATALOG = [
    {
        "name": "qwen3:4b",
        "display_name": "Qwen3 4B",
        "size_gb": 2.5,
        "min_ram_gb": 8,
        "description": "轻量模型，速度快，适合快速分析",
    },
    {
        "name": "qwen3:8b",
        "display_name": "Qwen3 8B",
        "size_gb": 5.0,
        "min_ram_gb": 16,
        "description": "平衡质量与速度，推荐大多数用户使用",
    },
    {
        "name": "qwen3:14b",
        "display_name": "Qwen3 14B",
        "size_gb": 9.0,
        "min_ram_gb": 32,
        "description": "最佳分析质量，适合高配机器",
    },
]


# ── Cloud provider presets ────────────────────────

CLOUD_PROVIDERS = [
    # 国产模型
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "api_format": "openai",
    },
    {
        "id": "minimax",
        "name": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "default_model": "MiniMax-M2.5",
        "models": ["MiniMax-M2.5", "MiniMax-M2.5-highspeed", "MiniMax-M2.1", "MiniMax-Text-01"],
        "api_format": "openai",
    },
    {
        "id": "qwen",
        "name": "阿里云百炼（Qwen）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-max",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-long"],
        "api_format": "openai",
    },
    {
        "id": "moonshot",
        "name": "Moonshot / Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-32k",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "api_format": "openai",
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4",
        "models": ["glm-4", "glm-4-air", "glm-4-flash"],
        "api_format": "openai",
    },
    {
        "id": "siliconflow",
        "name": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "Qwen/Qwen2.5-72B-Instruct",
        "models": ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3", "Qwen/QwQ-32B"],
        "api_format": "openai",
    },
    {
        "id": "yi",
        "name": "零一万物（Yi）",
        "base_url": "https://api.lingyiwanwu.com/v1",
        "default_model": "yi-large",
        "models": ["yi-large", "yi-large-turbo", "yi-medium"],
        "api_format": "openai",
    },
    # 海外模型
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
        "api_format": "openai",
    },
    {
        "id": "anthropic",
        "name": "Anthropic（Claude）",
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-5",
        "models": ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"],
        "api_format": "anthropic",
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.0-flash",
        "models": ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
        "api_format": "openai",
    },
    # 自定义
    {
        "id": "custom",
        "name": "自定义",
        "base_url": "",
        "default_model": "",
        "models": [],
        "api_format": "openai",
    },
]


class PullModelRequest(BaseModel):
    model: str


class SetDefaultModelRequest(BaseModel):
    model: str


class CloudConfigRequest(BaseModel):
    provider: str
    base_url: str
    model: str
    api_key: str


class ValidateCloudRequest(BaseModel):
    base_url: str
    api_key: str
    provider: str = ""  # provider id，用于识别 Anthropic 等特殊格式


class SwitchModeRequest(BaseModel):
    mode: str  # "ollama" or "openai"
    ollama_model: str | None = None


class BudgetRequest(BaseModel):
    monthly_budget_cny: float


@router.get("")
async def get_settings():
    from src.infra import config

    return {
        "settings": {
            "llm_provider": config.LLM_PROVIDER,
            "llm_model": get_model_name(),
            "ollama_base_url": OLLAMA_BASE_URL,
            "ollama_model": config.OLLAMA_MODEL,
            "required_model": REQUIRED_MODEL,
            "context_window": config.CONTEXT_WINDOW_SIZE,
        }
    }


@router.get("/health-check")
async def health_check():
    """Check LLM connectivity — always returns both Ollama and cloud status."""
    from src.infra import config

    ollama_result = await _check_ollama()
    openai_result = await _check_openai()
    # Merge: ollama fields as base, overlay cloud fields, set active provider
    merged = {**ollama_result, **openai_result}
    merged["llm_provider"] = config.LLM_PROVIDER
    merged["llm_model"] = config.get_model_name()
    merged["llm_base_url"] = config.LLM_BASE_URL
    return merged


@router.post("/ollama/start")
async def start_ollama():
    """Attempt to start Ollama and wait for it to become available."""
    if shutil.which("ollama") is None:
        return {"success": False, "error": "Ollama 未安装"}

    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(
                ["open", "-a", "Ollama"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as e:
        return {"success": False, "error": f"启动失败: {str(e)[:200]}"}

    # Poll up to 5 seconds for Ollama to become reachable
    for _ in range(10):
        await asyncio.sleep(0.5)
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
                if resp.status_code == 200:
                    return {"success": True}
        except Exception:
            pass

    return {"success": False, "error": "Ollama 已启动但未在 5 秒内就绪"}


def _get_total_ram_gb() -> float:
    """Get total system RAM in GB using os.sysconf (macOS/Linux)."""
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return round(total / (1024**3), 1)
    except (ValueError, OSError):
        return 0.0


@router.get("/hardware")
async def get_hardware():
    """Return system hardware info for model recommendations."""
    return {
        "total_ram_gb": _get_total_ram_gb(),
        "platform": platform.system(),
        "arch": platform.machine(),
    }


@router.get("/ollama/recommendations")
async def get_model_recommendations():
    """Return recommended models based on system RAM."""
    ram_gb = _get_total_ram_gb()

    # Determine which models are already installed
    installed_names: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                for m in resp.json().get("models", []):
                    installed_names.add(m.get("name", ""))
    except Exception:
        pass

    recommendations = []
    for model in MODEL_CATALOG:
        if model["min_ram_gb"] > ram_gb and ram_gb > 0:
            continue
        recommended = False
        if ram_gb >= 32 and model["name"] == "qwen3:14b":
            recommended = True
        elif 16 <= ram_gb < 32 and model["name"] == "qwen3:8b":
            recommended = True
        elif ram_gb < 16 and model["name"] == "qwen3:4b":
            recommended = True

        installed = any(
            n == model["name"] or n.startswith(model["name"].split(":")[0] + ":")
            and n.endswith(model["name"].split(":")[1])
            for n in installed_names
        )

        recommendations.append({
            **model,
            "recommended": recommended,
            "installed": installed,
        })

    return {
        "total_ram_gb": ram_gb,
        "recommendations": recommendations,
    }


@router.post("/ollama/pull")
async def pull_ollama_model(req: PullModelRequest):
    """Pull an Ollama model with SSE streaming progress."""

    async def event_stream():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_BASE_URL}/api/pull",
                    json={"name": req.model, "stream": True},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                            if data.get("status") == "success":
                                break
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'error': str(e)[:200]})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/ollama/default-model")
async def set_default_model(req: SetDefaultModelRequest):
    """Set the default Ollama model for analysis."""
    from src.db.sqlite_db import get_connection
    from src.infra import config

    conn = await get_connection()
    try:
        await conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ('ollama_default_model', ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (req.model,),
        )
        await conn.commit()
    finally:
        await conn.close()

    # Also update runtime config
    config.OLLAMA_MODEL = req.model

    # Re-detect context window for the new model
    from src.infra.context_budget import detect_and_update_context_window
    await detect_and_update_context_window()

    return {"success": True, "model": req.model}


# ── Cloud LLM configuration ─────────────────────────


@router.get("/cloud/providers")
async def get_cloud_providers():
    """Return list of cloud LLM provider presets."""
    return {"providers": CLOUD_PROVIDERS}


@router.get("/cloud/config")
async def get_cloud_config():
    """Return current cloud LLM configuration (API key masked)."""
    from src.db.sqlite_db import get_connection
    from src.infra.secret_store import load_api_key

    # Load provider/model/base_url from app_settings
    config = {"provider": "", "base_url": "", "model": "", "has_api_key": False}
    conn = await get_connection()
    try:
        for key in ("cloud_provider", "cloud_base_url", "cloud_model"):
            row = await conn.execute(
                "SELECT value FROM app_settings WHERE key=?",
                (key,),
            )
            result = await row.fetchone()
            short_key = key.replace("cloud_", "")
            config[short_key] = result[0] if result else ""
    finally:
        await conn.close()

    api_key = await load_api_key()
    config["has_api_key"] = bool(api_key)
    if api_key:
        config["api_key_masked"] = api_key[:4] + "****" + api_key[-4:] if len(api_key) > 8 else "****"
    else:
        config["api_key_masked"] = ""

    return config


@router.post("/cloud/config")
async def save_cloud_config(req: CloudConfigRequest):
    """Save cloud LLM configuration and update runtime config."""
    from src.db.sqlite_db import get_connection
    from src.infra.secret_store import save_api_key

    # Save API key securely
    storage = await save_api_key(req.api_key)

    # Save provider/model/base_url to app_settings
    conn = await get_connection()
    try:
        for key, value in [
            ("cloud_provider", req.provider),
            ("cloud_base_url", req.base_url),
            ("cloud_model", req.model),
        ]:
            await conn.execute(
                """INSERT INTO app_settings (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, value),
            )
        await conn.commit()
    finally:
        await conn.close()

    # Hot-update runtime config
    from src.infra.config import update_cloud_config

    update_cloud_config(
        provider="openai",
        api_key=req.api_key,
        base_url=req.base_url,
        model=req.model,
    )

    # Re-detect context window for the new cloud config
    from src.infra.context_budget import detect_and_update_context_window
    await detect_and_update_context_window()

    return {"success": True, "storage": storage}


@router.post("/cloud/validate")
async def validate_cloud_api(req: ValidateCloudRequest):
    """Test cloud LLM API connectivity with provided credentials."""
    if not req.api_key or not req.base_url:
        return {"valid": False, "error": "API Key 和 Base URL 不能为空"}

    # 判断是否为 Anthropic 协议
    is_anthropic = req.provider == "anthropic" or "anthropic.com" in req.base_url

    try:
        base = req.base_url.rstrip("/")
        transport = httpx.AsyncHTTPTransport()
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            if is_anthropic:
                headers = {
                    "x-api-key": req.api_key,
                    "anthropic-version": "2023-06-01",
                }
                resp = await client.get(f"{base}/v1/models", headers=headers)
            else:
                headers = {"Authorization": f"Bearer {req.api_key}"}
                # 先尝试 GET /models；部分供应商（MiniMax 等）不实现该端点
                resp = await client.get(f"{base}/models", headers=headers)
                if resp.status_code == 404:
                    # Fallback：用最小化 completions 请求探测鉴权是否有效
                    # 1 token 请求几乎无费用；401 = key 无效，其他状态 = 连接正常
                    probe = await client.post(
                        f"{base}/chat/completions",
                        headers={**headers, "Content-Type": "application/json"},
                        json={
                            "model": "__probe__",
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1,
                        },
                    )
                    if probe.status_code == 401:
                        return {"valid": False, "error": "API Key 无效（401 Unauthorized）"}
                    # 任何非 401 响应（包括 400/422 模型不存在）都说明 key 有效、接口可达
                    return {"valid": True}

            if resp.status_code == 200:
                return {"valid": True}
            elif resp.status_code == 401:
                return {"valid": False, "error": "API Key 无效（401 Unauthorized）"}
            else:
                return {"valid": False, "error": f"服务器返回 {resp.status_code}"}
    except httpx.ConnectError:
        return {"valid": False, "error": f"无法连接到 {req.base_url}"}
    except httpx.TimeoutException:
        return {"valid": False, "error": "连接超时（10秒）"}
    except Exception as e:
        return {"valid": False, "error": str(e)[:200]}


# ── Mode switching & advanced settings ──────────────


@router.post("/llm-mode")
async def switch_llm_mode(req: SwitchModeRequest):
    """Switch between Ollama and cloud LLM mode."""
    from src.db.sqlite_db import get_connection

    if req.mode not in ("ollama", "openai"):
        return {"success": False, "error": "无效模式，请选择 ollama 或 openai"}

    conn = await get_connection()
    try:
        await conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ('llm_mode', ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (req.mode,),
        )
        await conn.commit()
    finally:
        await conn.close()

    if req.mode == "ollama":
        from src.infra.config import switch_to_ollama

        model = req.ollama_model or "qwen3:8b"
        switch_to_ollama(model)
    else:
        # Cloud mode — load saved config
        from src.infra.config import update_cloud_config
        from src.infra.secret_store import load_api_key

        conn = await get_connection()
        try:
            cloud_cfg: dict[str, str] = {}
            for key in ("cloud_base_url", "cloud_model"):
                row = await conn.execute(
                    "SELECT value FROM app_settings WHERE key=?",
                    (key,),
                )
                result = await row.fetchone()
                cloud_cfg[key] = result[0] if result else ""
        finally:
            await conn.close()

        api_key = await load_api_key() or ""
        update_cloud_config(
            provider="openai",
            api_key=api_key,
            base_url=cloud_cfg.get("cloud_base_url", ""),
            model=cloud_cfg.get("cloud_model", ""),
        )

    # Re-detect context window for the new mode/model
    from src.infra.context_budget import detect_and_update_context_window
    await detect_and_update_context_window()

    return {"success": True, "mode": req.mode}


@router.get("/running-tasks")
async def get_running_tasks():
    """Return the count of currently running analysis tasks."""
    from src.services.analysis_service import get_analysis_service

    service = get_analysis_service()
    return {"running_count": len(service._active_loops)}


@router.post("/restore-defaults")
async def restore_defaults():
    """Restore LLM config to defaults: local Ollama + qwen3:8b."""
    from src.db.sqlite_db import get_connection
    from src.infra.config import switch_to_ollama, update_max_tokens

    # Clear persisted LLM settings
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM app_settings WHERE key IN "
            "('llm_mode', 'ollama_default_model', 'llm_max_tokens')",
        )
        await conn.commit()
    finally:
        await conn.close()

    switch_to_ollama("qwen3:8b")
    update_max_tokens(8192)

    # Re-detect context window for default model
    from src.infra.context_budget import detect_and_update_context_window
    await detect_and_update_context_window()

    return {"success": True}


@router.get("/budget")
async def get_budget():
    """Get monthly budget and current month usage."""
    from src.services.cost_service import get_monthly_budget, get_monthly_usage

    budget = await get_monthly_budget()
    usage = await get_monthly_usage()
    return {
        "monthly_budget_cny": budget,
        "monthly_used_cny": usage.get("cny", 0.0),
        "monthly_used_usd": usage.get("usd", 0.0),
        "monthly_input_tokens": usage.get("input_tokens", 0),
        "monthly_output_tokens": usage.get("output_tokens", 0),
    }


@router.post("/budget")
async def save_budget(req: BudgetRequest):
    """Set monthly budget in CNY."""
    from src.services.cost_service import set_monthly_budget

    if req.monthly_budget_cny < 0:
        return {"success": False, "error": "预算不能为负数"}
    await set_monthly_budget(req.monthly_budget_cny)
    return {"success": True, "monthly_budget_cny": req.monthly_budget_cny}


# ── Benchmark: ~3000-char chapter text for realistic extraction ────────

_BENCHMARK_CHAPTER_TEXT = """\
韩立盘坐在黄枫谷后山的一处隐秘洞府中，手中捏着一枚散发着淡淡灵光的筑基丹。这枚丹药是他花了三年时间，走遍了坠星峡谷和万妖山外围，冒着生命危险收集灵药材料，最终求得墨大夫亲手炼制而成。洞府四壁镶嵌着数十枚聚灵石，淡青色的灵气缓缓向中央汇聚，在韩立身周形成了一层薄薄的灵气漩涡。

"师父，弟子准备突破了。"韩立对坐在对面的墨大夫恭敬说道。

墨大夫捋了捋花白的胡须，面色凝重道："韩立，筑基是修仙路上第一道天堑。你虽然修炼长春功多年，灵力也算深厚，但筑基的凶险不在于灵力是否充足，而在于你能否在灵力冲击经脉时保持神识清明。为师当年也是九死一生才侥幸成功。"

他顿了顿，又补充道："长春功虽然入门容易，但后期进境极慢。好在此功法有一个独特之处——修炼者的灵力浑厚程度远超同阶，这对冲击筑基反而是优势。你要记住，药力运转到紫府时千万不可急躁，宁可多耗费一些时间，也要确保每一条经脉都被灵力充分洗练。"

韩立点了点头，将筑基丹吞入腹中。一股温热的药力从丹田升起，沿着经脉缓缓运转。他按照长春功的行功路线引导药力，先走任脉，再转督脉，最后汇聚于紫府。

就在药力运行到第三个周天时，一阵剧烈的疼痛从经脉中传来。韩立咬紧牙关，额头上豆大的汗珠不断滚落。墨大夫在一旁密切注视着，手中已经准备好了一枚护脉丹，以防万一。这枚护脉丹同样出自墨大夫之手，用万妖山特产的血灵芝为主料炼制，能在经脉即将破裂时迅速修复损伤。

时间一分一秒过去，韩立的气息忽强忽弱，灵力波动越来越剧烈。洞府外，负责守护的厉飞雨和南宫婉也感受到了这股波动。

"韩师弟的灵压好强……"南宫婉喃喃道，秀眉微蹙，"但似乎很不稳定。"

厉飞雨沉声道："韩师弟的资质虽非上佳，但胜在心志坚定。墨大夫又亲自坐镇，应该不会有事。倒是我担心另一件事——前几日从越国和岚国边境传来消息，血煞宗的人最近频繁出没于落日峰附近。我在坠星峡谷执行任务时，亲眼见到两名黑袍修士鬼鬼祟祟地在峡谷南段探查，虽然没能看清面目，但那股阴森的血煞之气绝不会错。"

"血煞宗？"南宫婉神色一变，"他们不是被七玄门联合几大门派围剿过一次了吗？怎么又冒出来了？"

厉飞雨摇了摇头："据七玄门的钟卫安长老传信，血煞宗宗主赵无极不但没死，反而趁乱夺了一件上古法宝——噬魂幡。此宝能收摄修士魂魄，炼化后可大增修为，极为邪门。赵无极凭借此宝，已从结丹初期一举突破到结丹中期。更可怕的是，据钟长老调查，赵无极为了修炼噬魂幡，暗中残害了至少三十名散修，其中不乏筑基中期的高手。"

南宫婉面色凝重："结丹中期……整个黄枫谷也只有谷主令狐冲和大长老两人是这个境界。如果赵无极真的来犯——"

"所以钟卫安长老已经向黄枫谷发出了联盟请求。"厉飞雨接话道，"令狐冲掌门三天前已经同意，并且指派了灵兽山的陈师叔率领十名筑基期弟子前往坠星峡谷驻守。七玄门那边也派出了以钟卫安长老为首的精锐小队，在落日峰一带布下了警戒阵法。两家联手，至少在坠星峡谷方向可以形成一道防线。"

南宫婉稍稍松了口气，但随即又想到什么："那万妖山方向呢？黄枫谷的东面可是完全敞开的……"

厉飞雨苦笑道："这正是最让人头疼的地方。万妖山妖兽众多，又不受人控制，万一有人故意驱使妖兽——"

话音未落，洞府中突然传来一声清越的长啸。一道耀眼的灵光从洞口激射而出，直冲天际。韩立的气息在刹那间发生了质变——原本浑浊的灵力变得清澈透明，经脉中的灵力运转速度提升了数倍。洞府周围的聚灵石因灵力涌动过于剧烈，纷纷碎裂开来，化为一地粉末。

"成了！筑基成功了！"墨大夫大喜过望，忍不住仰天长笑。

韩立缓缓睁开双眼，感受着体内焕然一新的灵力，心中既欣喜又感慨。筑基之后，他能清晰地感受到天地间游离的灵气——以前如同雾中观花，如今却如同置身于灵气的海洋中，每一丝灵气的流动都纤毫毕现。他站起身来，向墨大夫深深一揖："多谢师父护法之恩。弟子能有今日，全赖师父多年栽培。"

墨大夫摆了摆手："你的成功是你自己挣来的。不过现在还不是高兴的时候，你刚突破筑基，境界尚未稳固，需要至少半个月的闭关巩固。为师已经在百药园为你备好了几味灵药——两株五十年份的紫阳花、一瓶凝元露，还有三枚培元丹，待你出关后来取。另外，我还为你准备了一卷御器术的入门心法，筑基之后方可修炼此术，御使法器飞行，行动速度可提升数倍。"

这时厉飞雨和南宫婉也走了进来，纷纷向韩立道贺。韩立注意到南宫婉手中拿着一封传音符，便问道："南宫师姐，可是有什么消息？"

南宫婉将传音符递给墨大夫："墨大夫，这是乌龟岛陈巧倩师姐发来的急信。万妖山深处近来妖气大盛，赤焰蟒的领地附近发现了四级妖兽活动的痕迹，还有多具散修的残骸——都是被吸干了精血而死。陈师姐怀疑，有人在暗中驱使妖兽或以散修血祭妖兽，意图不明。她已经知会了彩霞山的云霄子前辈，请他密切关注万妖山北麓的动静。"

墨大夫的脸色瞬间阴沉下来："四级妖兽……那可是相当于结丹期修士的存在。万妖山距离黄枫谷不过三百里，如果那些妖兽被驱赶过来……而且你说散修的尸体是被吸干精血的？这种手法倒是与血煞宗的邪功如出一辙。"

厉飞雨面色微变："墨大夫的意思是，赵无极可能在万妖山和坠星峡谷两个方向同时布局？"

墨大夫缓缓点头："不排除这个可能。血煞宗覆灭之前，赵无极就以狡诈多变著称。他若真的恢复了实力，绝不会只从一个方向进攻。我这就去面见谷主，商议对策。韩立，你安心闭关，莫要分心。"

韩立虽然刚刚筑基成功，但听到这些消息，心中也不禁涌起一股沉重感。修仙界从来都不太平，越国境内的几大门派——黄枫谷、七玄门、灵兽山——虽然表面上和平共处，但暗地里的争斗从未停止。而血煞宗这个邪修势力的死灰复燃，更是给本就紧张的局势雪上加霜。

他望向洞府外的天空，彩霞山的轮廓在夕阳下显得格外壮美。远处是连绵的万妖山脉，那片深不可测的原始山林中不知隐藏着多少危险。万妖山以西是坠星峡谷，峡谷的另一边便是岚国的势力范围。越国和岚国虽然名义上互不侵犯，但修仙界的纷争从来不以凡人的国界为限。

墨大夫临走前又叮嘱了一句："对了，张铁也在百药园修炼，他前些日子象甲功突破了第六层，正是春风得意的时候。你闭关时若有什么需要，可以找他帮忙。此外，百药园后面的灵泉洞灵气浓度极高，是闭关巩固境界的绝佳之地——为师已经为你预留了半月的使用权。"

韩立感激地点了点头。他环顾了一下已经破碎的洞府，聚灵石的粉末散落一地，墙壁上还留着灵力冲击造成的裂纹。他暗暗下定决心：等境界稳固后，一定要尽快修炼御器术，提升实力。无论是为了自保，还是为了报答墨大夫的养育之恩，更或是为了在即将到来的风暴中保护同门师兄弟，他都不能停下脚步。

厉飞雨拍了拍韩立的肩膀："韩师弟，安心闭关，外面的事有我们顶着。等你出关，咱们一起去坠星峡谷看看那些血煞宗的鼠辈。"

南宫婉微微一笑："飞雨师兄说的不错。韩师弟你现在最重要的事就是稳固境界，切不可急于求成。"

韩立郑重地向二人抱拳："多谢两位师兄师姐，韩立记下了。"\
"""

_BENCHMARK_INPUT_CHARS = len(_BENCHMARK_CHAPTER_TEXT)
_ESTIMATED_CHAPTER_CHARS = 3000

# ── Fixed entity dictionary for benchmark (same format as context_summary_builder output) ────────

_BENCHMARK_ENTITY_DICT = """\
## 已知实体词典（来自预扫描）
以下是本书已确认的实体名称，请在提取时优先使用这些名称：

【人物】韩立（别名：二愣子）| 墨大夫 | 厉飞雨 | 南宫婉 | 令狐冲 | 钟卫安 | 赵无极 | 陈巧倩 | 张铁 | 云霄子
【组织】黄枫谷 | 七玄门 | 血煞宗 | 灵兽山
【地点】越国 | 岚国 | 坠星峡谷 | 万妖山 | 落日峰 | 黄枫谷后山 | 百药园 | 乌龟岛 | 彩霞山 | 灵泉洞
【物品】筑基丹 | 噬魂幡 | 护脉丹 | 传音符 | 聚灵石
【概念】长春功 | 筑基 | 结丹 | 象甲功 | 御器术\
"""

# ── Golden standard for quality evaluation ────────

_GOLDEN_STANDARD = {
    "characters": [
        "韩立", "墨大夫", "厉飞雨", "南宫婉", "令狐冲",
        "钟卫安", "赵无极", "陈巧倩", "张铁", "云霄子",
    ],
    "locations": [
        "黄枫谷", "坠星峡谷", "万妖山", "落日峰", "百药园",
        "越国", "岚国", "乌龟岛", "彩霞山", "灵泉洞",
    ],
    "organizations": ["血煞宗", "七玄门", "灵兽山"],
    "key_relations": [
        ("韩立", "墨大夫"),
        ("韩立", "厉飞雨"),
        ("韩立", "南宫婉"),
        ("赵无极", "血煞宗"),
        ("钟卫安", "七玄门"),
        ("令狐冲", "黄枫谷"),
        ("韩立", "张铁"),
    ],
}


def _evaluate_quality(llm_output) -> dict:
    """Evaluate LLM output quality against golden standard.

    Accepts dict (structured output) or str (cloud text fallback).
    """
    # Build a flat text for entity/relation name matching
    if isinstance(llm_output, dict):
        # Structured output — extract names from known fields
        text_parts: list[str] = []
        for ch in llm_output.get("characters", []):
            if isinstance(ch, dict):
                text_parts.append(ch.get("name", ""))
                text_parts.extend(ch.get("new_aliases", []))
            else:
                text_parts.append(str(ch))
        for loc in llm_output.get("locations", []):
            if isinstance(loc, dict):
                text_parts.append(loc.get("name", ""))
            else:
                text_parts.append(str(loc))
        for rel in llm_output.get("relationships", []):
            if isinstance(rel, dict):
                text_parts.append(rel.get("person_a", ""))
                text_parts.append(rel.get("person_b", ""))
            else:
                text_parts.append(str(rel))
        for org in llm_output.get("org_events", []):
            if isinstance(org, dict):
                text_parts.append(org.get("org_name", ""))
            else:
                text_parts.append(str(org))
        # Also dump the entire dict as text for fallback matching
        flat_text = " ".join(text_parts) + " " + json.dumps(llm_output, ensure_ascii=False)
    else:
        flat_text = str(llm_output)

    all_entities = (
        _GOLDEN_STANDARD["characters"]
        + _GOLDEN_STANDARD["locations"]
        + _GOLDEN_STANDARD["organizations"]
    )
    found = [e for e in all_entities if e in flat_text]
    missed = [e for e in all_entities if e not in flat_text]
    entity_recall = len(found) / len(all_entities) if all_entities else 0

    rel_found = 0
    for a, b in _GOLDEN_STANDARD["key_relations"]:
        if a in flat_text and b in flat_text:
            rel_found += 1
    total_rels = len(_GOLDEN_STANDARD["key_relations"])
    relation_recall = rel_found / total_rels if total_rels else 0

    overall = round((entity_recall * 0.6 + relation_recall * 0.4) * 100, 1)
    notes = [f"漏掉: {e}" for e in missed] if missed else []

    return {
        "overall_score": overall,
        "entity_recall": round(entity_recall * 100, 1),
        "relation_recall": round(relation_recall * 100, 1),
        "notes": notes,
    }


@router.post("/model-benchmark")
async def run_model_benchmark():
    """Run a realistic benchmark using the full extraction pipeline."""
    from src.db.sqlite_db import get_connection
    from src.extraction.chapter_fact_extractor import (
        _build_extraction_schema,
        _load_examples,
        _load_system_prompt,
    )
    from src.infra.context_budget import get_budget
    from src.infra.llm_client import get_llm_client, LLMError
    from src.infra.openai_client import OpenAICompatibleClient

    model = get_model_name()
    provider = LLM_PROVIDER

    try:
        client = get_llm_client()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

    is_cloud = isinstance(client, OpenAICompatibleClient)
    budget = get_budget()

    # ── Build real extraction prompt ──
    system_template = _load_system_prompt()
    context = _BENCHMARK_ENTITY_DICT + "\n\n（无前序上下文）"
    system = system_template.replace("{context}", context)

    # Cloud: embed schema in system prompt (same as _call_and_parse)
    schema = _build_extraction_schema()
    if is_cloud:
        schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
        system += (
            f"\n\n## 输出 JSON Schema\n"
            f"你必须严格按照以下 JSON Schema 输出，不要输出多余字段或文本：\n"
            f"```json\n{schema_text}\n```"
        )

    # Build user prompt with one few-shot example
    examples = _load_examples()
    example_text = ""
    if examples:
        examples_json = json.dumps([examples[0]], ensure_ascii=False, indent=2)
        example_text = f"## 参考示例\n```json\n{examples_json}\n```\n\n"

    user_prompt = (
        f"{example_text}"
        f"## 第 1 章\n\n{_BENCHMARK_CHAPTER_TEXT}\n\n"
        "【关键要求】\n"
        "1. characters：宁多勿漏！包含所有有名字或固定称呼的人物\n"
        "2. relationships：任何两个人物有互动或提及关系都必须提取，evidence 引用原文\n"
        "3. locations：宁多勿漏！所有具体地名都必须提取\n"
        "4. events：每个事件的 participants 列出参与者姓名，location 填写地点\n"
        "5. spatial_relationships：提取地点间的方位/距离/包含/相邻关系\n"
        "6. world_declarations：当文中有世界宏观结构描述时必须提取，没有则输出空列表\n"
        "7. new_concepts：功法、丹药、修炼体系等首次出现的概念，definition 必须详细\n"
        "8. 只提取原文明确出现的内容，禁止编造\n"
    )

    max_out = LLM_MAX_TOKENS if is_cloud else 8192
    timeout = 120 if is_cloud else 300

    start = time.time()
    try:
        result, usage = await client.generate(
            system=system,
            prompt=user_prompt,
            format=schema,
            temperature=0.1,
            max_tokens=max_out,
            timeout=timeout,
            num_ctx=budget.extraction_num_ctx,
        )
    except LLMError as e:
        raise HTTPException(status_code=503, detail=f"模型调用失败: {e}")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"未知错误: {e}")

    elapsed_ms = int((time.time() - start) * 1000)
    output_tokens = usage.completion_tokens
    tokens_per_second = round(output_tokens / (elapsed_ms / 1000), 1) if elapsed_ms > 0 else 0

    # Benchmark text ≈ real chapter length, so elapsed ≈ chapter time
    estimated_chapter_s = round(elapsed_ms / 1000 * (_ESTIMATED_CHAPTER_CHARS / _BENCHMARK_INPUT_CHARS), 1)

    # Quality evaluation
    quality = _evaluate_quality(result)

    # Auto-save to benchmark_records
    try:
        conn = await get_connection()
        try:
            await conn.execute(
                """INSERT INTO benchmark_records
                   (model, provider, context_window, elapsed_ms, input_tokens, output_tokens,
                    tokens_per_second, estimated_chapter_time_s, estimated_chapter_chars,
                    quality_score, quality_detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    model, provider, CONTEXT_WINDOW_SIZE, elapsed_ms,
                    usage.prompt_tokens, output_tokens, tokens_per_second,
                    estimated_chapter_s, _ESTIMATED_CHAPTER_CHARS,
                    quality["overall_score"],
                    json.dumps(quality, ensure_ascii=False),
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
    except Exception:
        pass  # Don't fail benchmark if save fails

    return {
        "model": model,
        "provider": provider,
        "context_window": CONTEXT_WINDOW_SIZE,
        "benchmark": {
            "elapsed_ms": elapsed_ms,
            "input_tokens": usage.prompt_tokens,
            "output_tokens": output_tokens,
            "tokens_per_second": tokens_per_second,
            "estimated_chapter_time_s": estimated_chapter_s,
            "estimated_chapter_chars": _ESTIMATED_CHAPTER_CHARS,
        },
        "quality": quality,
    }


@router.get("/model-benchmark/history")
async def get_benchmark_history():
    """Return recent benchmark records."""
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        rows = await conn.execute_fetchall(
            """SELECT id, model, provider, context_window, elapsed_ms,
                      tokens_per_second, estimated_chapter_time_s,
                      quality_score, created_at
               FROM benchmark_records
               ORDER BY created_at DESC
               LIMIT 50"""
        )
        records = [
            {
                "id": r[0],
                "model": r[1],
                "provider": r[2],
                "context_window": r[3],
                "elapsed_ms": r[4],
                "tokens_per_second": r[5],
                "estimated_chapter_time_s": r[6],
                "quality_score": r[7],
                "created_at": r[8],
            }
            for r in rows
        ]
        return records
    finally:
        await conn.close()


@router.delete("/model-benchmark/history/{record_id}")
async def delete_benchmark_record(record_id: int):
    """Delete a benchmark record."""
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM benchmark_records WHERE id = ?", (record_id,)
        )
        await conn.commit()
        return {"success": True}
    finally:
        await conn.close()


async def _check_ollama() -> dict:
    """Check Ollama installation, connectivity, and available models."""
    ollama_installed = shutil.which("ollama") is not None

    result: dict = {
        "llm_provider": "ollama",
        "llm_model": get_model_name(),
        "ollama_running": False,
        "ollama_status": "not_installed" if not ollama_installed else "installed_not_running",
        "ollama_url": OLLAMA_BASE_URL,
        "required_model": REQUIRED_MODEL,
        "model_available": False,
        "available_models": [],
    }

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                result["ollama_running"] = True
                result["ollama_status"] = "running"
                data = resp.json()
                models_raw = data.get("models", [])
                result["available_models"] = [
                    {
                        "name": m.get("name", ""),
                        "size": m.get("size", 0),
                        "modified_at": m.get("modified_at", ""),
                    }
                    for m in models_raw
                ]
                model_names = [m.get("name", "") for m in models_raw]
                result["model_available"] = any(
                    m == REQUIRED_MODEL
                    or m.startswith(REQUIRED_MODEL + ":")
                    or (
                        REQUIRED_MODEL.startswith(m.split(":")[0])
                        and m.split(":")[0] == REQUIRED_MODEL.split(":")[0]
                    )
                    for m in model_names
                )
    except (httpx.ConnectError, httpx.TimeoutException, Exception) as e:
        result["error"] = str(e)[:200]

    return result


async def _check_openai() -> dict:
    """Check OpenAI-compatible API connectivity."""
    from src.infra import config

    result: dict = {
        "llm_base_url": config.LLM_BASE_URL,
        "api_available": False,
    }

    if not config.LLM_API_KEY or not config.LLM_BASE_URL:
        result["cloud_error"] = "API Key 和 Base URL 未配置"
        return result

    try:
        base = config.LLM_BASE_URL.rstrip("/")
        headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"}
        transport = httpx.AsyncHTTPTransport()
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as client:
            resp = await client.get(f"{base}/models", headers=headers)
            result["api_available"] = resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, Exception) as e:
        result["cloud_error"] = str(e)[:200]

    return result
